"""
gatekeeper.py: Make bundled helper binaries (macserial, ocvalidate, ...) runnable
even when macOS has put them under quarantine.

When the patcher (or its source tree / payloads.dmg) is downloaded through a
browser, macOS tags it with the 'com.apple.quarantine' extended attribute and
every file extracted from it, or read from a disk image mounted from it,
inherits that tag. The OpenCore utilities we ship (e.g. macserial-non-t2) are
not notarized, so as soon as we try to exec one of them Gatekeeper either
shows its "cannot be opened" dialog and kills the process, or simply refuses
to launch it. subprocess.run() then returns with a signal/non-zero exit code
and no output, which aborts the whole OpenCore build/installation.

prepare_executable() clears the quarantine attribute (and restores the
executable bit) on the binary before it is run. If the file can't be modified
in place (read-only volume), a clean copy is made in a private temporary
directory and that copy's path is returned instead.

Only use this for binaries that ship inside the patcher's own payloads.
This module deliberately imports nothing from the patcher itself so that
constants.py can use it without causing a circular import.
"""

import os
import atexit
import shutil
import logging
import tempfile
import subprocess

from pathlib import Path


QUARANTINE_XATTR = "com.apple.quarantine"
_XATTR_BINARY    = "/usr/bin/xattr"

# Original path -> path that is safe to execute (may be a temporary copy)
_prepared: dict = {}
_temp_dir: Path = None


def _has_quarantine(path: Path) -> bool:
    try:
        result = subprocess.run(
            [_XATTR_BINARY, "-p", QUARANTINE_XATTR, str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15
        )
    except Exception:
        return False
    return result.returncode == 0


def _strip_quarantine(path: Path) -> None:
    try:
        subprocess.run(
            [_XATTR_BINARY, "-d", QUARANTINE_XATTR, str(path)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15
        )
    except Exception as e:
        logging.warning(f"Failed to clear {QUARANTINE_XATTR} on {path}: {e}")


def _make_executable(path: Path) -> None:
    if os.access(path, os.X_OK):
        return
    try:
        os.chmod(path, os.stat(path).st_mode | 0o111)
    except OSError as e:
        logging.warning(f"Failed to mark {path} as executable: {e}")


def _is_ready(path: Path) -> bool:
    return os.access(path, os.X_OK) and not _has_quarantine(path)


def _get_temp_dir() -> Path:
    global _temp_dir
    if _temp_dir is None or not _temp_dir.exists():
        _temp_dir = Path(tempfile.mkdtemp(prefix="OpenCore-Patcher-T2-tools-"))
        atexit.register(shutil.rmtree, str(_temp_dir), True)
    return _temp_dir


def _copy_to_temp(path: Path) -> Path:
    destination = _get_temp_dir() / path.name
    # copyfile() only copies the file's data, never its extended attributes,
    # so the copy starts out without the quarantine tag.
    shutil.copyfile(path, destination)
    os.chmod(destination, 0o755)
    if _has_quarantine(destination):
        _strip_quarantine(destination)
    return destination


def prepare_executable(path: Path) -> Path:
    """
    Return a path to 'path' that Gatekeeper will let us execute.

    Never raises: if anything goes wrong the original path is returned, so the
    caller's own error handling still applies.
    """
    path = Path(path)

    # Nothing to do on non-macOS hosts (e.g. Linux CI) or for missing files
    if not Path(_XATTR_BINARY).exists() or not path.is_file():
        return path

    cached = _prepared.get(str(path))
    if cached is not None and Path(cached).is_file() and _is_ready(Path(cached)):
        return Path(cached)

    try:
        if not _is_ready(path):
            logging.info(f"Clearing quarantine from bundled tool: {path.name}")
            _strip_quarantine(path)
            _make_executable(path)

        if _is_ready(path):
            _prepared[str(path)] = path
            return path

        # Read-only location (e.g. a disk image mounted without a shadow file)
        logging.info(f"Could not clear quarantine in place, using a temporary copy of {path.name}")
        copy = _copy_to_temp(path)
        if _is_ready(copy):
            _prepared[str(path)] = copy
            return copy
    except Exception as e:
        logging.warning(f"Failed to prepare {path} for execution: {e}")

    return path
