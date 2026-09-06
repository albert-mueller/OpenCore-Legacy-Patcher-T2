"""
dmg_mount.py: PatcherSupportPkg DMG Mounting. Handles Universal-Binaries and DortaniaInternalResources DMGs.
"""

import logging
import subprocess
import applescript
import sys
from pathlib import Path
from ... import constants
from ...support import subprocess_wrapper

class PatcherSupportPkgMount:

    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants
        self.icon_path = str(self.constants.app_icon_path).replace("/", ":")[1:]

    def _request_admin_password(self) -> str:
        """Prompt for the local administrator password via a plain dialog.

        Deliberately NOT routed through "do shell script ... with administrator
        privileges": that mechanism runs the elevated command via
        /usr/libexec/security_authtrampoline, a process detached from the
        current login/Aqua session. hdiutil's own internal authentication
        (DIHelperAgentMaster) appears to depend on that session being present,
        so a hdiutil invocation elevated via the trampoline can fail with
        "hdiutil: attach failed - Authentication error" even though the same
        command run under sudo from a session-bound process succeeds. A plain
        "display dialog" only needs a WindowServer session to render, not the
        trampoline's separate authorization session, so we use it purely to
        collect the password and feed it to sudo ourselves.
        """
        try:
            return applescript.AppleScript(
                f'set theResult to display dialog "OpenCore Legacy Patcher requires administrator access to mount patch resources." default answer "" with hidden answer with title "OpenCore Legacy Patcher" with icon file "{self.icon_path}"\nreturn the text returned of theResult'
            ).run()
        except Exception:
            return ""

    def _run_hdiutil(self, dmg_path: Path, mount_point: Path, shadow_path: Path = None, password: str = None, retry_on_auth_error: bool = False) -> subprocess.CompletedProcess:
        """Helper to standardize hdiutil execution using -stdinpass, with elevation on failure"""
        return subprocess_wrapper.mount_dmg(
            dmg_path, mount_point, shadow_path=shadow_path, password=password,
            admin_password_prompt=self._request_admin_password,
            retry_on_auth_error=retry_on_auth_error
        )

    def _mount_universal_binaries_dmg(self) -> bool:
        """Mount PatcherSupportPkg's Universal-Binaries.dmg"""
        dmg_path = Path(self.constants.payload_local_binaries_root_path_dmg)
        if not dmg_path.exists():
            logging.error("- PatcherSupportPkg resources missing, Patcher likely corrupted!!!")
            logging.exception("Stack Trace:")
            return False

        output = self._run_hdiutil(
            dmg_path,
            Path(self.constants.payload_path / "Universal-Binaries"),
            shadow_path=Path(self.constants.payload_path / "Universal-Binaries_overlay"),
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
                f'set theResult to display dialog "{msg}" default answer "" with hidden answer with title "OpenCore Legacy Patcher" with icon file "{self.icon_path}"\nreturn the text returned of theResult'
            ).run()
        except Exception:
            return ""

    def _display_authentication_error(self) -> None:
        applescript.AppleScript(f'display dialog "Failed to mount DortaniaInternal resources, please file an internal radar." with title "OpenCore Legacy Patcher" with icon file "{self.icon_path}"').run()

    def _display_too_many_attempts(self) -> None:
        applescript.AppleScript(f'display dialog "Failed to mount DortaniaInternal resources, too many incorrect passwords." with title "OpenCore Legacy Patcher" with icon file "{self.icon_path}"').run()

    def mount(self) -> bool:
        if Path(self.constants.payload_local_binaries_root_path).exists():
            return True
        return self._mount_universal_binaries_dmg() and self._mount_dortania_internal_resources_dmg()
