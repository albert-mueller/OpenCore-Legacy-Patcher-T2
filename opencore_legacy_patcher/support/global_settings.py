"""
global_settings.py: Library for querying and writing global environment settings

Alternative to Apple's 'defaults' tool
Store data in '/Users/Shared'
This is to ensure compatibility when running without a user
ie. during automated patching
"""

import logging
import plistlib
import os
from pathlib import Path


# Location of the settings file, exposed so callers can name it in user facing errors
# (ie. "run 'sudo rm <path>'") without hardcoding a second copy of the path.
SETTINGS_FILE_NAME:  str = ".com.dortania.opencore-legacy-patcher.plist"
SETTINGS_FOLDER:     str = "/Users/Shared"
SETTINGS_PLIST_PATH: str = f"{SETTINGS_FOLDER}/{SETTINGS_FILE_NAME}"


class GlobalEnviromentSettings:
    """
    Library for querying and writing global environment settings
    """

    # A fresh instance of this class is created on every read/write call
    # throughout the app. If the settings file is stuck in a state we
    # can't recover from, we still want to warn the user - but only once
    # per process, instead of on every single property access.
    _warned_inaccessible: bool = False

    def __init__(self) -> None:
        self.file_name:              str = SETTINGS_FILE_NAME
        self.global_settings_folder: str = SETTINGS_FOLDER
        self.global_settings_plist:  str = SETTINGS_PLIST_PATH

        self._generate_settings_file()
        self._convert_defaults_to_global_settings()


    def _owning_uid(self) -> int:
        """
        UID the settings file is supposed to belong to.

        Unprivileged runs: ourselves.

        Elevated runs (the macos-update / os-caching LaunchDaemons, the
        auto-patcher, or the app started from source via sudo) have no
        settings of their own - they act on behalf of the GUI user, so the
        file has to keep belonging to that user. SUDO_UID covers the sudo
        case, /dev/console covers LaunchDaemons, which inherit no such
        environment.
        """
        if os.geteuid() != 0:
            return os.getuid()

        sudo_uid = os.environ.get("SUDO_UID", "")
        if sudo_uid.isdigit() and int(sudo_uid) != 0:
            return int(sudo_uid)

        try:
            console_uid = os.stat("/dev/console").st_uid
            if console_uid != 0:
                return console_uid
        except Exception:
            pass

        return 0


    def _trusted_uids(self) -> list:
        """
        Owners we accept for the settings file.

        The GUI user is trusted even while we run as root: rejecting it there
        made every elevated run treat the user's own settings file as hostile,
        delete it, and hand back an empty one - wiping every "GUI:*" key the
        user had saved.
        """
        return list({0, os.getuid(), self._owning_uid()})


    def _finalize_permissions(self) -> None:
        """
        Keep the file private (0600) *and* owned by the GUI user.

        Without the chown, an elevated write leaves a root-owned 0600 file
        behind that no later unprivileged run can read or write, so settings
        silently stop persisting until it is removed by hand.
        """
        try:
            os.chmod(self.global_settings_plist, 0o600)
            owner = self._owning_uid()
            if os.geteuid() == 0 and owner != 0:
                os.chown(self.global_settings_plist, owner, -1)
        except Exception as e:
            logging.error(f"Unable to set permissions on global settings file: {e}")


    def _file_is_accessible(self) -> bool:
        """
        True if the current process can actually read AND write the
        settings file, regardless of who nominally owns it.

        Ownership alone ('owned by root or by us') isn't enough: a file
        can be 'trusted' by that check and still be unusable. The most
        common case is a settings file that was created (or last written)
        by a prior *root* invocation of this same code - eg. running the
        patcher via sudo while testing root-patching logic from source,
        or the auto-patcher's LaunchDaemon running as root - which leaves
        it owned by root with mode 0600. A later unprivileged run then
        passes the ownership check (uid 0 is explicitly trusted) but
        every open() call still raises PermissionError, since only root
        can read/write a 0600 file it doesn't own.
        """
        return os.access(self.global_settings_plist, os.R_OK | os.W_OK)


    def _warn_inaccessible_once(self) -> None:
        """
        Emits a single, actionable warning instead of letting every
        read_property/write_property/delete_property call (each of
        which constructs its own instance of this class) print its own
        copy of the same PermissionError.
        """
        if GlobalEnviromentSettings._warned_inaccessible:
            return
        GlobalEnviromentSettings._warned_inaccessible = True

        logging.error("CRITICAL: Global settings file exists but cannot be read or written by this user.")
        logging.error("This usually happens after the patcher was previously run as root (eg. via sudo).")
        logging.error("Please run the following command in Terminal, then restart the app:")
        logging.error(f"    sudo rm '{self.global_settings_plist}'")


    def read_property(self, property_name: str) -> str:
        """
        Reads a property from the global settings file
        """
        if Path(self.global_settings_plist).is_symlink():
            logging.warning("Security Alert: Symlink detected during read. Ignoring.")
            return None

        if Path(self.global_settings_plist).exists():
            # Security: Verify ownership before loading data
            file_info = os.stat(self.global_settings_plist)
            if file_info.st_uid not in self._trusted_uids():
                logging.error("Security Error: Settings file is owned by an untrusted user.")
                return None

            if not self._file_is_accessible():
                self._warn_inaccessible_once()
                return None

            try:
                plist = plistlib.load(Path(self.global_settings_plist).open("rb"))
                if property_name in plist:
                    return plist[property_name]
            except Exception as e:
                logging.error("Error: Unable to read global settings file")
                logging.error(e)
                return None
        return None


    def delete_property(self, property_name: str) -> bool:
        """
        Deletes a property from the global settings file

        Returns whether the settings file is known to no longer hold the property.
        Callers that need the removal to stick (ie. a choice made in the GUI) can act
        on a False instead of assuming it was stored - a failure here is silent
        otherwise, and the setting simply reappears on the next read.
        """
        if not Path(self.global_settings_plist).exists():
            # Nothing to delete from, and _generate_settings_file() already reported
            # why the file could not be created
            return False

        # Security: Verify ownership
        file_info = os.stat(self.global_settings_plist)
        if file_info.st_uid not in self._trusted_uids():
            logging.error("Security Error: Settings file is owned by an untrusted user.")
            return False

        if not self._file_is_accessible():
            self._warn_inaccessible_once()
            return False

        try:
            plist = plistlib.load(Path(self.global_settings_plist).open("rb"))
            if property_name not in plist:
                return True
            del plist[property_name]
            plistlib.dump(plist, Path(self.global_settings_plist).open("wb"))
            self._finalize_permissions()
            return True
        except Exception as e:
            logging.error("Error: Unable to modify global settings file")
            logging.error(e)
            return False


    def write_property(self, property_name: str, property_value) -> bool:
        """
        Writes a property to the global environment settings

        Returns whether the value actually reached the settings file. Every failure
        path here is recoverable-looking to the caller but not to the user: the write
        is dropped, the next read returns the old value, and whatever they just chose
        in the GUI quietly reverts. Callers that surface a result to the user should
        check this rather than assume the write succeeded.
        """
        # Security: Destroy symlinks
        if Path(self.global_settings_plist).is_symlink():
            logging.warning("Security Alert: Symlink detected. Unlinking.")
            Path(self.global_settings_plist).unlink()

        if not Path(self.global_settings_plist).exists():
            # _generate_settings_file() runs in __init__ and already logged why it
            # could not create the file
            logging.error("Failed to write to global settings file: file is missing")
            return False

        # Security: Verify ownership
        file_info = os.stat(self.global_settings_plist)
        if file_info.st_uid not in self._trusted_uids():
            logging.error("Security Error: Settings file is owned by an untrusted user.")
            return False

        if not self._file_is_accessible():
            self._warn_inaccessible_once()
            return False

        try:
            plist = plistlib.load(Path(self.global_settings_plist).open("rb"))
            plist[property_name] = property_value

            plistlib.dump(plist, Path(self.global_settings_plist).open("wb"))
            self._finalize_permissions()
            return True
        except Exception as e:
            logging.error("Failed to write to global settings file")
            logging.error(e)
            return False


    def _generate_settings_file(self) -> None:
        """
        Initializes the settings file and handles ownership/permission conflicts
        """
        path = Path(self.global_settings_plist)

        # 1. Clear Symlinks
        if path.is_symlink():
            path.unlink()

        # 2. Ownership/Permission Conflict Resolution (Self-Healing)
        if path.exists():
            file_info = os.stat(self.global_settings_plist)
            owner_untrusted = file_info.st_uid not in self._trusted_uids()

            # A "trusted" owner (root, or ourselves) doesn't guarantee this
            # process can actually open the file - eg. a root-owned 0600
            # file left behind by a prior elevated run. Treat that the
            # same way as an untrusted owner: attempt to self-heal.
            if owner_untrusted or not self._file_is_accessible():
                # Only log the first time we hit this in the process - a fresh
                # instance of this class is created on every read/write call,
                # so without this guard every single property access would
                # print its own copy of the same warning.
                if not GlobalEnviromentSettings._warned_inaccessible:
                    if owner_untrusted:
                        logging.warning("Untrusted settings file detected. Attempting to remove...")
                    else:
                        logging.warning("Settings file is inaccessible to the current user (likely owned by root from a prior elevated run). Attempting to remove...")
                try:
                    # Attempt to remove the file if we have directory write access.
                    # Retried on every call (cheap) so we self-heal immediately if
                    # the underlying permissions get fixed while the app is running.
                    path.unlink()
                except PermissionError:
                    # If we fail, tell the user to use sudo. /Users/Shared has
                    # the sticky bit set, so only the file's owner or root can
                    # remove it, regardless of directory write permissions.
                    self._warn_inaccessible_once()
                    return

        # 3. Create fresh file if missing
        if not path.exists():
            try:
                Path(self.global_settings_folder).mkdir(parents=True, exist_ok=True)
                plistlib.dump({"Developed by Dortania": True}, path.open("wb"))
                self._finalize_permissions()
            except (PermissionError, OSError) as e:
                logging.info(f"Unable to initialize global settings file: {e}")


    def _convert_defaults_to_global_settings(self) -> None:
        """
        Converts legacy defaults to global settings
        """
        defaults_path = Path("~/Library/Preferences/com.dortania.opencore-legacy-patcher.plist").expanduser()

        if defaults_path.exists():
            # If the global settings file exists but this process can't
            # actually read/write it, don't attempt the migration - we'd
            # just fail on the open() call below and spam "Error during
            # settings migration" on every instantiation. Leave the legacy
            # defaults file in place so migration can succeed later once
            # the underlying permissions issue is resolved.
            if Path(self.global_settings_plist).exists() and not self._file_is_accessible():
                self._warn_inaccessible_once()
                return

            try:
                defaults_plist = plistlib.load(defaults_path.open("rb"))

                if Path(self.global_settings_plist).exists():
                    global_settings_plist = plistlib.load(Path(self.global_settings_plist).open("rb"))
                else:
                    global_settings_plist = {}

                global_settings_plist.update(defaults_plist)

                plistlib.dump(global_settings_plist, Path(self.global_settings_plist).open("wb"))
                self._finalize_permissions()

                defaults_path.unlink()
            except Exception as e:
                logging.error("Error during settings migration")
                logging.error(e)
