"""
logging_handler.py: Initialize logging framework for program
"""

import os
import sys
import pprint
import logging
import threading
import traceback
import subprocess
import applescript

from pathlib import Path
from datetime import datetime

from logging.handlers import RotatingFileHandler

from .. import constants

from . import subprocess_wrapper


class InitializeLoggingSupport:
    """
    Initialize logging framework for program

    Primary responsibilities:
    - Determine where to store log file
    - Clean log file if it's near the max file size
    - Initialize logging framework configuration
    - Implement custom traceback handler
    - Implement error handling for file write

    Usage:
    >>> from resources.logging_handler import InitializeLoggingSupport
    >>> InitializeLoggingSupport()

    FOR DEVELOPERS:
    - Do not invoke logging until after '_attempt_initialize_logging_configuration()' has been invoked

    """

    def __init__(self, global_constants: constants.Constants) -> None:
        self.constants: constants.Constants = global_constants

        log_time = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")

        self.log_filename: str  = f"OpenCore-Patcher-T2_{self.constants.patcher_version}_{log_time}.log"
        self.log_filepath: Path = None

        self.original_excepthook:        sys       = sys.excepthook
        self.original_thread_excepthook: threading = threading.excepthook

        self.max_file_size:     int = 1024 * 1024               # 1 MB
        self.file_size_redline: int = 1024 * 1024 - 1024 * 100  # 900 KB, when to start cleaning log file

        self._initialize_logging_path()
        self._attempt_initialize_logging_configuration()
        self._start_logging()
        self._implement_custom_traceback_handler()
        self._clean_prior_version_logs()


    # Per-user log folder, mirroring the install directory of the patcher
    # ('/Library/Application Support/albert-mueller/OpenCore-Patcher-T2').
    # Lives in ~/Library/Logs rather than next to the app, as the GUI runs
    # unprivileged and cannot write to /Library.
    LOG_DIRECTORY: Path = Path("~/Library/Logs/albert-mueller/OpenCore-Patcher-T2")

    # Shared with Dortania's patcher, only read to move our own logs out of it.
    LEGACY_LOG_DIRECTORY: Path = Path("~/Library/Logs/Dortania")

    # Every log this fork writes starts with this, Dortania's start with
    # "OpenCore-Patcher_" - so logs of their patcher are never touched.
    LOG_PREFIX: str = "OpenCore-Patcher-T2_"


    def _initialize_logging_path(self) -> None:
        """
        Initialize logging framework storage path
        """

        base_path = Path("~/Library/Logs").expanduser()
        if not base_path.exists() or str(base_path).startswith("/var/root/"):
            # Likely in an installer environment, store in /Users/Shared
            base_path = Path("/Users/Shared")
        else:
            base_path = self.LOG_DIRECTORY.expanduser()
            try:
                base_path.mkdir(parents=True, exist_ok=True)
                self._migrate_legacy_logs(base_path)
            except Exception as e:
                print(f"Failed to create log folder {base_path}: {e}")
                base_path = Path("/Users/Shared")

        self.log_filepath = Path(f"{base_path}/{self.log_filename}").expanduser()
        self.constants.log_filepath = self.log_filepath


    def _migrate_legacy_logs(self, destination: Path) -> None:
        """
        Move our logs out of ~/Library/Logs/Dortania

        Only files carrying our prefix are moved, and the folder is only removed
        once empty, so Dortania's own patcher keeps its logs.
        """
        legacy = self.LEGACY_LOG_DIRECTORY.expanduser()
        if not legacy.is_dir():
            return

        for file in legacy.glob(f"{self.LOG_PREFIX}*.log"):
            try:
                file.rename(destination / file.name)
            except Exception as e:
                print(f"Failed to move {file} to {destination}: {e}")

        try:
            legacy.rmdir() # Refuses non-empty folders
        except OSError:
            pass


    def _clean_prior_version_logs(self) -> None:
        """
        Clean logs from old Patcher versions

        Keep 10 latest logs
        """

        paths = [
            self.log_filepath.parent,                       # ~/Library/Logs/albert-mueller/OpenCore-Patcher-T2
            Path("~/Library/Logs").expanduser(),            # Old location
            self.LEGACY_LOG_DIRECTORY.expanduser(),         # Previous location, if moving failed
        ]

        logs = []

        for path in paths:
            if not path.is_dir():
                continue
            for file in path.glob(f"{self.LOG_PREFIX}*"):
                if not file.is_file():
                    continue

                if not file.name.endswith(".log"):
                    continue

                if file.name == self.log_filename:
                    continue

                logs.append(file)

        logs.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        for log in logs[9:]:
            try:
                log.unlink()
            except Exception as e:
                logging.error(f"Failed to delete log file: {e}")


    def _initialize_logging_configuration(self, log_to_file: bool = True) -> None:
        """
        Initialize logging framework configuration

        StreamHandler's format is used to mimic the default behavior of print()
        While FileHandler's format is for more in-depth logging

        Parameters:
            log_to_file (bool): Whether to log to file or not

        """

        # Configure basic stream handler. Use force=True to ensure we replace
        # any handlers that were configured earlier (entry scripts sometimes
        # call basicConfig which prevents later reconfiguration).
        try:
            logging.basicConfig(
                level=logging.NOTSET,
                format="[%(asctime)s] [%(filename)-32s] [%(lineno)-4d]: %(message)s",
                handlers=[logging.StreamHandler(stream = sys.stdout)],
                force=True,
            )
        except TypeError:
            # Older Python may not support force; fall back to clearing handlers
            root = logging.getLogger()
            for h in list(root.handlers):
                root.removeHandler(h)
            logging.basicConfig(
                level=logging.NOTSET,
                format="[%(asctime)s] [%(filename)-32s] [%(lineno)-4d]: %(message)s",
                handlers=[logging.StreamHandler(stream = sys.stdout)],
            )

        logger = logging.getLogger()
        logger.setLevel(logging.INFO)

        # Ensure the stream handler uses a simple message-only formatter to
        # mimic print() behavior in the terminal.
        if len(logger.handlers) > 0:
            logger.handlers[0].setFormatter(logging.Formatter("%(message)s"))

        # Attach a rotating file handler if requested and not already present.
        if log_to_file is True:
            existing_file_handlers = [h for h in logger.handlers if hasattr(h, 'baseFilename') and str(getattr(h, 'baseFilename')) == str(self.log_filepath)]
            if not existing_file_handlers:
                try:
                    rotating = RotatingFileHandler(str(self.log_filepath), maxBytes=self.max_file_size, backupCount=5)
                    rotating.setFormatter(logging.Formatter("[%(asctime)s] [%(filename)-32s] [%(lineno)-4d]: %(message)s"))
                    logger.addHandler(rotating)
                except Exception as e:
                    # If we fail to create a file handler (permission issue, etc.),
                    # log to stdout only and continue.
                    print(f"Failed to attach file logger: {e}")

        # If a file-like handler exists, we may want to set rotation limits
        for h in logger.handlers:
            if isinstance(h, RotatingFileHandler):
                h.maxBytes = self.max_file_size

    def _attempt_initialize_logging_configuration(self) -> None:
        """
        Attempt to initialize logging framework configuration

        If we fail to initialize the logging framework, we will disable logging to file
        """

        try:
            self._initialize_logging_configuration()
        except Exception as e:
            print(f"Failed to initialize logging framework: {e}")
            print("Retrying without logging to file...")
            self._initialize_logging_configuration(log_to_file=False)


    def _start_logging(self):
        """
        Start logging, used as easily identifiable start point in logs
        """

        str_msg = f"# OpenCore Legacy Patcher ({self.constants.patcher_version}) #"
        str_len = len(str_msg)

        logging.info('#' * str_len)
        logging.info(str_msg)
        logging.info('#' * str_len)

        logging.info("Log file set:")
        logging.info(f"  {self.log_filepath}")
        # Display relative path to avoid disclosing user's username
        try:
            path = self.log_filepath.relative_to(Path.home())
            logging.info(f"~/{path}")
        except ValueError:
            logging.info(self.log_filepath)


    def _implement_custom_traceback_handler(self) -> None:
        """
        Reroute traceback to logging module
        """

        def custom_excepthook(type, value, tb) -> None:
            """
            Reroute traceback in main thread to logging module
            """
            logging.error("Uncaught exception in main thread", exc_info=(type, value, tb))
            self._display_debug_properties()

            if "wx/" in "".join(traceback.format_exception(type, value, tb)):
                # Likely a GUI error, don't display error dialog
                return

            if self.constants.cli_mode is True:
                return

            error_msg = "OpenCore Legacy Patcher encountered the following internal error:\n\n"
            error_msg += f"{type.__name__}: {value}"
            if tb:
                error_msg += f"\n\n{traceback.extract_tb(tb)[-1]}"

            error_msg += "\n\nReveal log file?"

            # An exception message is not trusted input: it routinely echoes back a string
            # that came from disk or NVRAM. packaging's InvalidVersion, for one, embeds the
            # offending value verbatim ("Invalid version: '<value>'"), so a crafted version
            # in /System/Library/CoreServices/OpenCore-Legacy-Patcher.plist or in the
            # OCLP-Version NVRAM variable used to reach this dialog and break out of the
            # AppleScript string literal. Escape it, and cap the length so a long payload
            # (or a long legitimate traceback) cannot push the buttons off the dialog.
            if len(error_msg) > 500:
                error_msg = error_msg[:500] + "..."
            safe_error_msg = subprocess_wrapper.applescript_quote(error_msg)
            safe_version = subprocess_wrapper.applescript_quote(self.constants.patcher_version)

            # Ask user if they want to send crash report
            try:
                result = applescript.AppleScript(f'display dialog "{safe_error_msg}" with title "OpenCore Legacy Patcher ({safe_version})" buttons {{"Yes", "No"}} default button "Yes" with icon caution').run()
            except Exception as e:
                logging.error(f"Failed to display crash report dialog: {e}")
                return

            if result[applescript.AEType(b'bhit')] != "Yes":
                return

            subprocess.run(["/usr/bin/open", "--reveal", self.log_filepath])


        def custom_thread_excepthook(args) -> None:
            """
            Reroute traceback in spawned thread to logging module
            """
            logging.error("Uncaught exception in spawned thread", exc_info=(args))

        sys.excepthook = custom_excepthook
        threading.excepthook = custom_thread_excepthook


    def _display_debug_properties(self) -> None:
        """
        Display debug properties, primarily after main thread crash
        """
        logging.info("Host Properties:")
        logging.info(f"  XNU Version: {self.constants.detected_os}.{self.constants.detected_os_minor}")
        logging.info(f"  XNU Build: {self.constants.detected_os_build}")
        logging.info(f"  macOS Version: {self.constants.detected_os_version}")
        logging.info("Debug Properties:")
        logging.info(f"  Effective User ID: {os.geteuid()}")
        logging.info(f"  Effective Group ID: {os.getegid()}")
        logging.info(f"  Real User ID: {os.getuid()}")
        logging.info(f"  Real Group ID: {os.getgid()}")
        logging.info("  Arguments passed to Patcher:")
        for arg in sys.argv:
            logging.info(f"    {arg}")

        logging.info(f"Host Properties:\n{pprint.pformat(self.constants.computer, indent=4)}")
