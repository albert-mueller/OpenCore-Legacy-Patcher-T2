"""
dmg_mount.py: PatcherSupportPkg DMG Mounting. Handles Universal-Binaries and DortaniaInternalResources DMGs.
"""

import os
import logging
import subprocess
import applescript
import sys
from pathlib import Path
from ... import constants
from ...support import subprocess_wrapper


# Fixed passphrase Universal-Binaries.dmg is built with. Not a secret: it ships with
# the image and only keeps the payload opaque to Finder/Spotlight, so feeding it to
# hdiutil ourselves loses nothing and spares the user an unexplained system prompt.
UNIVERSAL_BINARIES_PASSPHRASE = "password"

class PatcherSupportPkgMount:

    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants
        # POSIX path, handed to AppleScript as 'with icon POSIX file'. The previous
        # HFS conversion (replace("/", ":")[1:]) produced a path whose first component
        # AppleScript reads as a volume name, so it never resolved - see
        # subprocess_wrapper.applescript_icon_clause().
        self.icon_path = self.constants.app_icon_path

    def _request_admin_password(self) -> str:
        """Prompt for the local administrator password. See subprocess_wrapper.request_admin_password()."""
        return subprocess_wrapper.request_admin_password(self.icon_path)

    def _run_hdiutil(self, dmg_path: Path, mount_point: Path, shadow_path: Path = None, password: str = None, retry_on_auth_error: bool = False) -> subprocess.CompletedProcess:
        """Helper to standardize hdiutil execution using -stdinpass, with elevation on failure"""
        return subprocess_wrapper.mount_dmg(
            dmg_path, mount_point, shadow_path=shadow_path, password=password,
            admin_password_prompt=self._request_admin_password,
            retry_on_auth_error=retry_on_auth_error
        )

    def _is_encrypted(self, dmg_path: Path) -> bool:
        """Whether hdiutil considers the image encrypted, ie. whether it will prompt for a passphrase at all.

        Deliberately fail-open: if the check cannot be run or its wording changes,
        assume encrypted and show the notice. A superfluous notice is harmless,
        a missing one leaves the user staring at an unanswerable system prompt.
        """
        try:
            result = subprocess.run(
                ["/usr/bin/hdiutil", "isencrypted", str(dmg_path)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30
            )
        except Exception as e:
            logging.error(f"- Failed to check if DMG is encrypted: {e}")
            return True
        output = result.stdout.decode(errors="ignore").lower()
        # hdiutil has printed both "encrypted: YES/NO" and "encrypted: 1/0" across releases.
        return not ("encrypted: no" in output or "encrypted: 0" in output)

    def _display_universal_binaries_password_notice(self) -> None:
        """Heads-up dialog shown before falling back to hdiutil's own passphrase prompt.

        Only reached when the built-in passphrase did not unlock the image. hdiutil
        then asks for it through a bare macOS system prompt that names only the disk
        image and gives no indication of what to type, which reads like an
        unexplained password request in the middle of root patching. State the
        passphrase ourselves beforehand so the prompt is answerable.
        """
        if self.constants.cli_mode is True:
            return
        try:
            applescript.AppleScript(
                f'display dialog "OpenCore Legacy Patcher could not unlock Universal-Binaries.dmg automatically.\\n\\nIf macOS asks for a password for this disk image, the password is:\\n\\n{UNIVERSAL_BINARIES_PASSPHRASE}" buttons {{"OK"}} default button "OK" with title "OpenCore Legacy Patcher"{subprocess_wrapper.applescript_icon_clause(self.icon_path)}'
            ).run()
        except Exception as e:
            logging.error(f"- Failed to display Universal-Binaries.dmg password notice: {e}")

    def _mount_universal_binaries_dmg(self) -> bool:
        """Mount PatcherSupportPkg's Universal-Binaries.dmg"""
        dmg_path = Path(self.constants.payload_local_binaries_root_path_dmg)
        if not dmg_path.exists():
            logging.error("- PatcherSupportPkg resources missing, Patcher likely corrupted!!!")
            return False

        mount_point = Path(self.constants.payload_path / "Universal-Binaries")
        shadow_path = Path(self.constants.payload_path / "Universal-Binaries_overlay")

        # Supply the passphrase ourselves rather than letting hdiutil put up its own
        # prompt. Skipped for unencrypted images: -stdinpass on those is pointless.
        output = self._run_hdiutil(
            dmg_path, mount_point, shadow_path=shadow_path,
            password=UNIVERSAL_BINARIES_PASSPHRASE if self._is_encrypted(dmg_path) else None,
            retry_on_auth_error=True
        )

        if output.returncode != 0:
            logging.info("- Failed to mount Universal-Binaries.dmg, retrying with interactive passphrase entry")
            subprocess_wrapper.log(output)

            # Fall back to hdiutil asking the user directly - covers an image built
            # with a different passphrase than the one hardcoded above. Tell them
            # what to type first, otherwise the system prompt is unanswerable.
            self._display_universal_binaries_password_notice()
            output = self._run_hdiutil(
                dmg_path, mount_point, shadow_path=shadow_path,
                retry_on_auth_error=True
            )

            if output.returncode != 0:
                logging.info("- Failed to mount Universal-Binaries.dmg")
                subprocess_wrapper.log(output)
                return False

        logging.info("- Mounted Universal-Binaries.dmg")
        return True

    def _mount_dortania_internal_resources_dmg(self) -> bool:
        """Mount PatcherSupportPkg's DortaniaInternalResources.dmg"""
        if not Path(self.constants.overlay_psp_path_dmg).exists() or \
           not Path("~/.dortania_developer").expanduser().exists() or \
           self.constants.cli_mode is True:
            return True

        logging.info("- Found DortaniaInternal resources, mounting...")

        for i in range(3):
            key = self._request_decryption_key(i)
            output = self._run_hdiutil(
                Path(self.constants.overlay_psp_path_dmg),
                Path(self.constants.payload_path / "DortaniaInternal"),
                password=key
            )

            if output.returncode != 0:
                logging.info("- Failed to mount DortaniaInternal resources")
                subprocess_wrapper.log(output)
                if "Authentication error" not in output.stdout.decode():
                    self._display_authentication_error()
                if i == 2:
                    self._display_too_many_attempts()
                    sys.exit(3)
                continue
            break

        logging.info("- Mounted DortaniaInternal resources")
        return self._merge_dortania_internal_resources()

    def _merge_dortania_internal_resources(self) -> bool:
        """Merge DortaniaInternal resources with Universal-Binaries"""
        result = subprocess.run(
            ["/usr/bin/ditto", str(self.constants.payload_path / "DortaniaInternal"), str(self.constants.payload_path / "Universal-Binaries")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
        return result.returncode == 0

    def _request_decryption_key(self, attempt: int) -> str:
        if attempt == 0 and Path("~/.dortania_developer_key").expanduser().exists():
            return Path("~/.dortania_developer_key").expanduser().read_text().strip()

        msg = "Welcome to the DortaniaInternal Program, please provide the decryption key." if attempt == 0 else f"Decryption failed. {2 - attempt} attempts remaining."
        try:
            return applescript.AppleScript(
                f'set theResult to display dialog "{subprocess_wrapper.applescript_quote(msg)}" default answer "" with hidden answer with title "OpenCore Legacy Patcher"{subprocess_wrapper.applescript_icon_clause(self.icon_path)}\nreturn the text returned of theResult'
            ).run()
        except Exception as e:
            logging.error(f"- Failed to prompt for decryption key: {e}")
            return ""

    def _display_authentication_error(self) -> None:
        applescript.AppleScript(f'display dialog "Failed to mount DortaniaInternal resources, please file an internal radar." with title "OpenCore Legacy Patcher"{subprocess_wrapper.applescript_icon_clause(self.icon_path)}').run()

    def _display_too_many_attempts(self) -> None:
        applescript.AppleScript(f'display dialog "Failed to mount DortaniaInternal resources, too many incorrect passwords." with title "OpenCore Legacy Patcher"{subprocess_wrapper.applescript_icon_clause(self.icon_path)}').run()

    def _resources_already_available(self) -> bool:
        """Whether Universal-Binaries is genuinely usable, rather than merely present.

        Checking only .exists() is not enough now that the attach can run under sudo:
        hdiutil creates the mountpoint as root, so an attach that fails after that
        point leaves an EMPTY, root-owned directory behind. Every later run then
        short-circuits here and root patching proceeds against no resources at all -
        and the user cannot clear the directory without sudo either.

        A real mount and a plain checked-out resources folder (source runs ship one)
        both stay valid; only the empty leftover is rejected, and hdiutil will simply
        mount over it.
        """
        path = Path(self.constants.payload_local_binaries_root_path)
        if not path.exists():
            return False

        if os.path.ismount(path):
            return True

        try:
            non_hidden_items = [item for item in path.iterdir() if not item.name.startswith(".")]
            if non_hidden_items:
                return True
        except OSError as error:
            # Unreadable (e.g. root-owned) - treat as unusable rather than assuming
            logging.error(f"- Could not inspect existing Universal-Binaries directory: {error}")
            return False

        logging.info("- Ignoring empty leftover Universal-Binaries directory, remounting")
        return False

    def mount(self) -> bool:
        if self._resources_already_available():
            return True
        return self._mount_universal_binaries_dmg() and self._mount_dortania_internal_resources_dmg()
