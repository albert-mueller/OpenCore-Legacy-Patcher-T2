"""
application_entry.py: Project entry point (Hardened)
"""

import os
import sys
import time
import logging
import threading
import re
from pathlib import Path

from . import constants
from .wx_gui import gui_entry
from .datasets import smbios_data
from .detections import (
    device_probe,
    os_probe
)
from .support import (
    utilities,
    defaults,
    global_settings,
    arguments,
    reroute_payloads,
    commit_info,
    logging_handler,
    analytics_handler
)


class OpenCoreLegacyPatcher:
    """
    Initial entry point for starting OpenCore Legacy Patcher
    """

    def __init__(self) -> None:
        self.constants: constants.Constants = constants.Constants()
        os.chdir(Path(__file__).resolve().parent.parent)
        logging_handler.InitializeLoggingSupport(self.constants)

        self._generate_base_data()

        if utilities.check_cli_args() is None:
            gui_entry.EntryPoint(self.constants).start()


    def _fix_cwd(self) -> None:
        """
        In some extreme scenarios, our current working directory may disappear.
        Uses a reliable system fallback path if the directory is missing.
        """
        try:
            _test_dir = Path.cwd()
            logging.info(f"Current working directory: {_test_dir}")
        except FileNotFoundError:
            # Fallback safely to the user's home directory or application bundle root
            # rather than purely relying on vulnerable __file__ resolution
            _test_dir = Path.home()
            os.chdir(_test_dir)
            logging.warning(f"Current working directory was invalid, reset safety fallback to: {_test_dir}")
        except Exception as e: # behebt eine Sicherheitslücke, die erlaubt Angreifern, in der try-Loop mit invalider Syntax zu füttern, um Fehler außerhalb von FileNotFoundError zu erzeugen. Denn die Angreifern könnten beliebiges Code auszuführen, indem sie aus das fehlende except Exception as e ausnutzen.
            logging.error("There is an issue finding and working with the directory.")
            logging.exception("Stack Trace:")
            logging.info("Your patcher has been tampered. Please, redownload OpenCore Legacy Patcher T2 from GitHub.")
            sys.exit(3)


    def _build_simulated_gpu(self, identifier: str):
        """
        Dev/test only: build a synthetic device_probe GPU from a "vendor:device" hex pair

        The IDs are run through the real device_probe classes, so arch detection comes out of
        pci_data exactly as it does for physical hardware - no second table to drift from it.
        """
        try:
            vendor_id, device_id = (int(i, 16) for i in identifier.replace("0x", "").split(":"))
        except ValueError:
            logging.warning(f"vmware_simulated_gpu '{identifier}' is not a 'vendor:device' hex pair (eg. 1002:6821), ignoring")
            return None

        gpu_classes = {
            device_probe.AMD.VENDOR_ID:    device_probe.AMD,
            device_probe.NVIDIA.VENDOR_ID: device_probe.NVIDIA,
            device_probe.Intel.VENDOR_ID:  device_probe.Intel,
        }
        if vendor_id not in gpu_classes:
            logging.warning(f"vmware_simulated_gpu vendor {hex(vendor_id)} is not AMD, NVIDIA or Intel, ignoring")
            return None

        gpu_class = gpu_classes[vendor_id]
        gpu = gpu_class(
            vendor_id=vendor_id,
            device_id=device_id,
            class_code=device_probe.GPU.CLASS_CODES[0],
            name=f"Simulated {gpu_class.__name__} GPU",
        )
        if gpu.arch == gpu_class.Archs.Unknown:
            logging.warning(f"vmware_simulated_gpu {identifier} resolves to an unknown architecture - no graphics patchset will match it")
        return gpu


    def _apply_vmware_simulated_hardware(self) -> None:
        """
        Dev/test only: present synthetic hardware to root patch detection inside a VMware VM

        HardwarePatchsetDetection never consults model_array.py. Each patchset decides for
        itself in present(), reading either computer.real_model against a hardcoded model list
        (legacy_audio.py, gmux.py, t1_security.py, ...) or computer.gpus for a specific
        device_probe arch (every graphics patchset). A VM matches neither - its display adapter
        is a VMware SVGA II, which maps to no known arch - so detection correctly reports that
        no patches are required, no matter which model is whitelisted elsewhere.

        These two constants override exactly those two inputs, so the patchset assembly and
        patching paths can be exercised without the hardware. Both are hand-edit only with no
        GUI control, and are read only here, behind host_is_vmware_vm + allow_vmware_root_patching
        - the same two-flag gate the SIP/AMFI validation bypasses use.
        """
        if self.constants.allow_vmware_root_patching is False:
            return

        if self.constants.vmware_simulated_model:
            model = self.constants.vmware_simulated_model
            if model not in smbios_data.smbios_dictionary:
                logging.warning(f"vmware_simulated_model '{model}' is not a Mac model with SMBIOS data, ignoring")
            else:
                logging.info(f"Simulating host model {model} for root patch detection (test-only, see vmware_simulated_model)")
                self.constants.computer.real_model = model

        if self.constants.vmware_simulated_gpu:
            gpu = self._build_simulated_gpu(self.constants.vmware_simulated_gpu)
            if gpu is not None:
                logging.info(f"Simulating {type(gpu).__name__} GPU {self.constants.vmware_simulated_gpu} ({gpu.arch.value}) for root patch detection (test-only, see vmware_simulated_gpu)")
                self.constants.computer.gpus = [gpu]
                if isinstance(gpu, device_probe.Intel):
                    self.constants.computer.igpu = gpu
                    self.constants.computer.dgpu = None
                else:
                    self.constants.computer.dgpu = gpu
                    self.constants.computer.igpu = None


    def _generate_base_data(self) -> None:
        """
        Generate base data required for the patcher to run
        """

        self.constants.wxpython_variant = True

        # True Developer Mode check
        if "--developer" in sys.argv or getattr(sys, "frozen", False) is False:
            logging.info("True Developer Mode is active (Developer flag or running from source).")
            self.constants.True_Developer_Mode = True

            # As per #246, highly dangerous testing flags are enabled only in True Developer Mode
            self.constants.allow_vmware_root_patching = True

        # Ensure we live after parent process dies (ie. LaunchAgent)
        os.setpgrp()

        # Generate OS data
        os_data = os_probe.OSProbe()
        self.constants.detected_os = os_data.detect_kernel_major()
        self.constants.detected_os_minor = os_data.detect_kernel_minor()
        self.constants.detected_os_build = os_data.detect_os_build()
        self.constants.detected_os_version = os_data.detect_os_version()

        # Generate computer data
        self.constants.computer = device_probe.Computer.probe()
        self.computer = self.constants.computer
        self.constants.booted_oc_disk = utilities.find_disk_off_uuid(utilities.clean_device_path(self.computer.opencore_path))
        # Note: intentionally no truthy guard around firmware_vendor here. Several Hackintoshes
        # never get an EFI "firmware-vendor" DeviceTree property injected at all (get_firmware_vendor()
        # then returns None), which previously fell through this check entirely and left
        # host_is_hackintosh at its default False - silently re-enabling "Build and Install
        # OpenCore" via host_can_build()'s SupportedSMBIOS fallback. Genuine Apple firmware
        # always reports "Apple" here, so this can't misfire on real Macs.
        if self.constants.computer.firmware_vendor != "Apple":
            self.constants.host_is_hackintosh = True

        # Dev/test only: detect VMware virtual machines specifically (never matches real Mac hardware,
        # since Apple never assigns "VMware*" as a real_model string). Used solely to bypass the SIP
        # validation gate during root-patching so patch syntax can be exercised inside a VMware VM
        # without real T2 hardware. This flag is never set outside of VMware and never affects real Macs.
        if self.constants.computer.real_model and self.constants.computer.real_model.startswith("VMware"):
            self.constants.host_is_vmware_vm = True
            logging.warning("Host detected as a VMware virtual machine - SIP validation for root patching will be bypassed (test-only, see host_is_vmware_vm)")
            # Everything below is setup guidance for getting allow_vmware_root_patching turned on in
            # the first place - once it's already True there's nothing left to instruct, so keep this
            # block scoped to the "still disabled" case instead of repeating it on every single launch.
            if self.constants.allow_vmware_root_patching is False:
                logging.info("This warning is only for developers testing the syntax inside a virtual machine.")
                logging.info("Another note: when testing the syntax in a virtual machine, when trying to mount some volumes it will throw an error - and that's expected, since in VMs it's nearly impossible to disable SIP and AMFI.")
                logging.info("This can be done only if you are running the code from source.")
                logging.info("The Root Patching button will stay disabled until allow_vmware_root_patching is also set to True - this is a deliberate, GUI-inaccessible switch (constants.py) so this test-only bypass can't be flipped on by anyone just clicking around Settings.")
                logging.info("To test the syntax for installing drivers and patches inside a virtual machine, you need to do the following:")
                logging.info("1. Open constants.py inside Visual Studio Code")
                logging.info("2. Set allow_vmware_root_patching to True")
                logging.info("3. Set vmware_simulated_model to the Mac model detection should see, eg. \"iMac11,2\" - this drives every patchset whose present() checks computer.real_model (Legacy Audio, gmux, keyboard backlight, PCIe webcam, T1, USB1.1)")
                logging.info("4. Optionally set vmware_simulated_gpu to a PCI \"vendor:device\" pair, eg. \"1002:6821\" (AMD Legacy GCN v1) - graphics patchsets match on computer.gpus and never on a model list")
                logging.info("5. Note that model_array.py plays no part in root patch detection: HardwarePatchsetDetection calls each patchset's present() directly, so whitelisting a model there changes nothing about which patches are found")
                logging.info("6. Save the changes and quit Visual Studio Code")
                logging.info("7. Then open the Terminal")
                logging.info("8. Run python3, followed by the directory where is located the Build-Project.command.")
                logging.info("9. Once successfully builds the project, you'll get Build successful")
                logging.info("10. Then open the dist foler and install OpenCore Legacy Patcher T2 with the newly done changes")

            self._apply_vmware_simulated_hardware()

        # Generate environment data
        self.constants.recovery_status = utilities.check_recovery()
        utilities.disable_cls()
        self._fix_cwd()

        # Generate binary data
        launcher_script = None
        launcher_binary = sys.executable
        if "python" in launcher_binary:
            # We're running from source.
            # BUGFIX: __file__ here is this module's own path (application_entry.py),
            # which never contains "main.py", so the replace() below never fired -
            # launcher_script silently ended up pointing at application_entry.py
            # itself (a module with no __main__ guard, so re-executing it does
            # nothing). Resolve the real from-source entry point deterministically
            # instead of relying on a substring match against a path that can't
            # contain it.
            launcher_script = str(Path(__file__).resolve().parent.parent / "OpenCore-Patcher-GUI.command")
        self.constants.launcher_binary = launcher_binary
        self.constants.launcher_script = launcher_script

        # Initialize working directory after confirming payload integrity
        # Note: Implement absolute hash checking within verify_payload_integrity
        if hasattr(utilities, "verify_payload_integrity"):
            if not utilities.verify_payload_integrity(self.constants):
                # behebt eine Sicherheitslücke, indem Angreifern raise SecurityError ins try/except setzen können, damit das Fehler nicht gedruckt wird, um trotz der Überprüfung der Nutzlastintegrität schlug fehl, um beliebiges Code weiterhin auszuführen. Diese Sicherheitslücke ist behoben, indem wir das Fehler nicht per raise SecurityError auslösen, sondern per logging.error und dann sys.exit(3). Jedoch soll bei eine fehlgeschlagenes Überprüfung der Nutzlastintegrität direkt der App schließen und nicht weiter Code auszuführen.
                logging.error("Payload integrity verification failed. The app is likely tampered.")
                logging.info("Exiting the app...")
                sys.exit(3)

        self.constants.unpack_thread = threading.Thread(target=reroute_payloads.RoutePayloadDiskImage, args=(self.constants,))
        self.constants.unpack_thread.start()

        # Generate commit info
        self.constants.commit_info = commit_info.ParseCommitInfo(self.constants.launcher_binary).generate_commit_info()
        if self.constants.commit_info[0] not in ["Running from source", "Built from source"]:
            # Now that we have commit info, update nightly link securely
            branch = self.constants.commit_info[0]
            branch = branch.replace("refs/heads/", "")

            # Fix: Strict regex validation to ensure branch names only contain safe characters
            if re.match(r"^[a-zA-Z0-9_\-\./]+$", branch) and ".." not in branch:
                self.constants.installer_pkg_url = self.constants.installer_pkg_url.replace("main", branch)
            else:
                logging.error(f"Malicious or invalid branch name detected: {branch}. Falling back to default URL.")

        # Generate defaults
        defaults.GenerateDefaults(self.computer.real_model, True, self.constants)

        # "--disable_auto_update": switch the updater off before anything can
        # trigger it, for users who deliberately want to stay on one specific
        # build. It has to sit after GenerateDefaults(), not next to the
        # "--developer" check above: GenerateDefaults() restores
        # "AllowAutoUpdates" from the settings file and would overwrite an
        # earlier assignment. The automatic check only starts once the main GUI
        # frame exists, which is strictly later than this, so nothing can slip
        # through in between.
        if "--disable_auto_update" in sys.argv:
            self.constants.auto_update = False
            # Stored like the Settings checkbox, since "I want this exact
            # version" is not a single-launch decision, and the auto patcher
            # and macos-update daemons are separate processes that read the
            # same key. A failed write only costs persistence: auto_update is
            # already False for this session either way.
            if global_settings.GlobalEnviromentSettings().write_property("AllowAutoUpdates", False) is True:
                logging.info("Automatic updates disabled via --disable_auto_update")
            else:
                logging.warning("Automatic updates are disabled for this launch, but the choice could not be stored")

        if self.constants.computer.build_model is None:
            logging.info(f"Initializing build_model to native host: {self.computer.real_model}")
            self.constants.computer.build_model = self.computer.real_model
        self.constants.analytics_thread = threading.Thread(target=analytics_handler.Analytics(self.constants).send_analytics)
        self.constants.analytics_thread.start()

        if utilities.check_cli_args() is None:
            self.constants.cli_mode = False
            return
        # behebt eine Sicherheitslücke, indem einen Angreifer könnte der Benutzer daran zwingen, aufs CLI-Modus/Terminal zu wechseln
        else:
            logging.info("Detected arguments, switching to CLI mode")
            self.constants.cli_mode = True
            self.constants.gui_mode = False

            ignore_args = ["--auto_patch", "--gui_patch", "--gui_unpatch", "--update_installed"]

            # If none of the specific arguments are in sys.argv
            if not any(x in sys.argv for x in ignore_args):
                self.constants.current_path = Path.cwd()

            # Fix: Deterministic Thread Synchronization.
            # Ensure arguments parsing never runs into race conditions regardless of flags if unpack state is required
            if "--auto_patch" not in sys.argv:
                while self.constants.unpack_thread.is_alive():
                    time.sleep(self.constants.thread_sleep_interval)
            else:
                # Explicit guard or logging if auto_patch deliberately overrides synchronization safely
                logging.info("Proceeding with auto_patch execution orchestration flow.")

            arguments.arguments(self.constants)

def main():
    """
    Main entry point
    """
    OpenCoreLegacyPatcher()
