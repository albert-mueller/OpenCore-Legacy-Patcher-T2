"""
reroute_payloads.py: Reroute binaries to tmp directory, and mount a disk image of the payloads
Implements a shadowfile to avoid direct writes to the dmg
"""

import atexit
import plistlib
import tempfile
import subprocess

import logging

from pathlib import Path

from . import subprocess_wrapper

from .. import constants


class RoutePayloadDiskImage:

    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants
        self._setup_tmp_disk_image()

    def _setup_tmp_disk_image(self) -> None:
        """
        Initialize temp directory and mount payloads.dmg
        Create overlay for patcher to write to

        Currently only applicable for GUI variant and not running from source
        """

        if self.constants.wxpython_variant is True and not self.constants.launcher_script:
            logging.info("Running in compiled binary, switching to tmp directory")
            self.temp_dir = tempfile.TemporaryDirectory()
            logging.info(f"New payloads location: {self.temp_dir.name}")
            logging.info("Creating payloads directory")
            Path(self.temp_dir.name / Path("payloads")).mkdir(parents=True, exist_ok=True)
            self._unmount_active_dmgs(unmount_all_active=False)
            output = subprocess_wrapper.mount_dmg(
                Path(self.constants.payload_path_dmg),
                Path(self.temp_dir.name / Path("payloads")),
                shadow_path=Path(self.temp_dir.name / Path("payloads_overlay")),
                password="password",
                # Reason shown in the native macOS authorization dialog; elevation itself is
                # handled by utilities.get_admin_permission() (Authorization Services), not by
                # collecting the password ourselves.
                admin_password_prompt="OpenCore-Patcher-T2 needs administrator permission to mount its internal payloads.",
                # Fixed, known-correct password: "Authentication error" here can only mean
                # the privilege gate/quarantine issue, never a wrong password (see mount_dmg)
                retry_on_auth_error=True
            )
            if output.returncode == 0:
                logging.info("Mounted payloads.dmg")
                self.constants.current_path = Path(self.temp_dir.name)
                self.constants.payload_path = Path(self.temp_dir.name) / Path("payloads")
                atexit.register(self._unmount_and_cleanup_tmp_dir)
            else:
                logging.info("Failed to mount payloads.dmg")
                subprocess_wrapper.log(output)


    def _unmount_and_cleanup_tmp_dir(self) -> None:
        """
        Unmount our payloads.dmg, then explicitly clean up the backing temp
        directory ourselves, in that fixed order, within this single atexit
        callback.

        tempfile.TemporaryDirectory() already schedules its own automatic
        cleanup via weakref.finalize the moment it's constructed, entirely
        independent of this atexit callback and with no guaranteed ordering
        relative to it. hdiutil detach returning doesn't guarantee the mount
        point is instantly free at the filesystem level (a real, if brief,
        gap - especially with a shadow-file overlay in play), so if that
        automatic finalizer fires first (or right on our heels), shutil.
        rmtree() can fail with "OSError: Resource busy" trying to remove the
        still-mounted 'payloads' subdirectory - surfacing as an uncaught
        exception during interpreter shutdown.

        Doing the unmount and the directory removal ourselves, via
        TemporaryDirectory.cleanup() (which marks its own finalizer as
        already fired), means the later automatic finalizer becomes a safe
        no-op instead of racing us.
        """
        self._unmount_active_dmgs(unmount_all_active=False)
        try:
            self.temp_dir.cleanup()
        except OSError as e:
            # Non-fatal: worst case this leaves a stray tmp directory behind
            # (macOS periodically clears /var/folders/*/T/ anyway) rather
            # than crashing the interpreter's own shutdown sequence.
            logging.warning(f"Failed to clean up temporary payloads directory: {e}")


    def _unmount_active_dmgs(self, unmount_all_active: bool = True) -> None:
        """
        Unmounts disk images associated with OCLP

        Finds all DMGs that are mounted, and forcefully unmount them
        If our disk image was previously mounted, we need to unmount it to use again
        This can happen if we crash during a previous secession, however 'atexit' class should hopefully avoid this

        Parameters:
            unmount_all_active (bool): If True, unmount all active DMGs, otherwise only unmount our own DMG
        """

        dmg_info = subprocess.run(["/usr/bin/hdiutil", "info", "-plist"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        dmg_info = plistlib.loads(dmg_info.stdout)


        for variant in ["DortaniaInternalResources.dmg", "Universal-Binaries.dmg", "payloads.dmg"]:
            for image in dmg_info["images"]:
                if image["image-path"].endswith(variant):
                    if unmount_all_active is False:
                        # Check that only our personal payloads.dmg is unmounted
                        if "shadow-path" in image:
                            if self.temp_dir.name in image["shadow-path"]:
                                logging.info(f"Unmounting personal {variant}")
                                subprocess.run(
                                    ["/usr/bin/hdiutil", "detach", image["system-entities"][0]["dev-entry"], "-force"],
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                                )
                    else:
                        logging.info(f"Unmounting {variant} at: {image['system-entities'][0]['dev-entry']}")
                        subprocess.run(
                            ["/usr/bin/hdiutil", "detach", image["system-entities"][0]["dev-entry"], "-force"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT
                        )