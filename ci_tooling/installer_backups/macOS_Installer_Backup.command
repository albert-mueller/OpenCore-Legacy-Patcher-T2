#!/usr/bin/env python3
"""
--------------------------------
macOS_Installer_Backup.command
--------------------------------
Utility for grabbing macOS Installers from Apple's catalogs and AppleDB,
and saving them to a local directory.

WARNING: Solely for internal usage, not intended for end-users.

Security notes:
- All remote data (Apple catalogs, AppleDB) is treated as untrusted.
- Version/build strings are validated before being used in file names,
  and every destination path is confirmed to stay inside the backup directory.
- Downloads are only accepted over HTTPS from Apple CDN hosts.
- Apple catalog downloads are validated against their chunklist; AppleDB
  downloads are validated against a published hash when one is available.
- Existing files are never silently overwritten.
"""

import os
import re
import sys
import time
import hashlib
import argparse

from pathlib import Path
from datetime import datetime
from urllib.parse import urlparse, urlunparse

# To allow easy importing of OpenCore Legacy Patcher's utilities
sys.path.append(str(Path(__file__).parent.parent.parent))

from opencore_legacy_patcher.support import (
    macos_installer_handler,
    network_handler,
    integrity_verification,
    utilities,
)
from opencore_legacy_patcher.datasets import os_data


_DEFAULT_PATH: str = "/Volumes/macOS Installers"

# Hosts that downloads are allowed to come from (subdomains included)
_TRUSTED_HOSTS: tuple = (
    "swcdn.apple.com",
    "swdist.apple.com",
    "updates.cdn-apple.com",
    "oscdn.apple.com",
)

# Allowed characters for any remote-derived file name component
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,127}$")

# Build numbers look like "20A5343j", "22F82", "25A354"
_SAFE_BUILD = re.compile(r"^[0-9]{2}[A-Z][0-9]{1,7}[a-z]?$")

# AppleDB hash keys we know how to verify, strongest first
_HASH_ALGORITHMS: tuple = (
    ("sha2-256", "sha256"),
    ("sha256",   "sha256"),
    ("sha1",     "sha1"),
)

_POLL_INTERVAL: float = 0.5

_DEFAULT_OSES: tuple = (
    os_data.os_data.big_sur,
    os_data.os_data.monterey,
    os_data.os_data.ventura,
    os_data.os_data.sonoma,
    os_data.os_data.sequoia,
    os_data.os_data.tahoe,
)


class UnsafeDataError(ValueError):
    """
    Raised when remote data would produce an unsafe path or URL
    """
    pass


def _safe_component(value) -> str:
    """
    Validate a remote-derived string before using it in a file name
    """
    if not isinstance(value, str) or not _SAFE_COMPONENT.match(value) or ".." in value:
        raise UnsafeDataError(f"Unsafe path component from remote data: {value!r}")
    return value


def _safe_build(value) -> str:
    """
    Validate a remote-derived build number
    """
    if not isinstance(value, str) or not _SAFE_BUILD.match(value):
        raise UnsafeDataError(f"Unexpected build number from remote data: {value!r}")
    return value


def _inside(base: Path, target: Path) -> Path:
    """
    Ensure target resolves to a location inside base
    """
    if not Path(target).resolve().is_relative_to(Path(base).resolve()):
        raise UnsafeDataError(f"Path escapes backup directory: {target}")
    return Path(target)


def _trusted_url(url) -> str:
    """
    Only allow Apple CDN hosts. Plain HTTP on a trusted host is upgraded to HTTPS.
    Returns the (possibly upgraded) URL, or raises UnsafeDataError.
    """
    if not isinstance(url, str):
        raise UnsafeDataError(f"Invalid URL: {url!r}")

    parsed = urlparse(url)
    host = parsed.hostname
    if host is None or not any(host == h or host.endswith("." + h) for h in _TRUSTED_HOSTS):
        raise UnsafeDataError(f"Untrusted download host: {url}")
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        raise UnsafeDataError(f"Unexpected URL components: {url}")

    if parsed.scheme == "http":
        parsed = parsed._replace(scheme="https", netloc=host)
    elif parsed.scheme != "https":
        raise UnsafeDataError(f"Unsupported URL scheme: {url}")

    return urlunparse(parsed)


def _url_suffix(url: str) -> str:
    """
    File suffix of a URL's path, ignoring query strings and fragments
    """
    return Path(urlparse(url).path).suffix


class InstallerBackup:

    def __init__(self,
                 directory: Path = Path(_DEFAULT_PATH),
                 supported_oses: tuple = _DEFAULT_OSES,
                 first_run: bool = False
                ) -> None:

        print(f"Starting macOS Installer Backup: {datetime.now()}")

        self._directory = Path(directory)
        self._supported_oses = tuple(supported_oses)

        self._os_table = {
            os_data.os_data.big_sur:  Path(self._directory, "11 Big Sur"),
            os_data.os_data.monterey: Path(self._directory, "12 Monterey"),
            os_data.os_data.ventura:  Path(self._directory, "13 Ventura"),
            os_data.os_data.sonoma:   Path(self._directory, "14 Sonoma"),
            os_data.os_data.sequoia:  Path(self._directory, "15 Sequoia"),
            os_data.os_data.tahoe:    Path(self._directory, "26 Tahoe"),
        }

        for os_version in self._supported_oses:
            if os_version not in self._os_table:
                raise ValueError(f"Unsupported OS version: {os_version}")

        # Avoid filling the boot disk if the backup volume isn't mounted
        if str(self._directory).startswith("/Volumes/"):
            volume_root = Path(*self._directory.parts[:3])
            if not os.path.ismount(volume_root):
                raise FileNotFoundError(f"Backup volume is not mounted: {volume_root}")

        for dir in self._os_table.values():
            if not Path(dir).exists():
                if first_run is False:
                    raise FileNotFoundError(f"Directory does not exist: {dir} (use --first-run to create)")
                Path(dir).mkdir(parents=True, exist_ok=True)

        self._main()


    def _destination(self, os_version: int, name: str) -> Path:
        """
        Build a destination path for an installer and verify it stays in the backup directory
        """
        if os_version not in self._os_table:
            raise UnsafeDataError(f"Unsupported OS version: {os_version}")
        return _inside(self._directory, Path(self._os_table[os_version], _safe_component(name)))


    def _download_installer(self, installer: dict) -> None:
        """
        Download installer
        """
        try:
            link = _trusted_url(installer["Link"])
            suffix = _url_suffix(link)
            installer_name = f"{_safe_component(installer['Version'])} ({_safe_build(installer['Build'])})"
            if suffix == ".pkg":
                installer_name += " InstallAssistant.pkg"
            elif suffix == ".ipsw":
                installer_name += " Restore.ipsw"
            else:
                raise UnsafeDataError(f"Unexpected installer type: {link}")
            integrity_name = f"{installer_name}.integrityDataV1"

            installer_path = self._destination(installer["OS"], installer_name)
            integrity_path = self._destination(installer["OS"], integrity_name)

            integrity = installer.get("integrity")
            if integrity is not None:
                integrity = _trusted_url(integrity)
        except (UnsafeDataError, KeyError) as e:
            print(f"Skipping installer: {e}")
            return

        if installer_path.exists():
            print(f"Refusing to overwrite existing {installer_path.name}")
            return

        print(f"Downloading {installer_name}")

        # Check if integrity file available
        if integrity is not None:
            result = self._downloader(url=integrity, path=integrity_path, name=integrity_name)
            if result is False:
                return

        # Download installer
        result = self._downloader(url=link, path=installer_path, name=installer_name)
        if result is False:
            return

        # Empty files were moved to "Dead URLs", nothing to validate
        if not installer_path.exists():
            return

        # Validate against chunklist (Apple catalog)
        if integrity is not None:
            self._validate_against_chunklist(installer_path=installer_path, chunklist=integrity_path)
            return

        # Validate against published hash (AppleDB)
        hashes = installer.get("Hashes") or {}
        if hashes:
            self._validate_against_hash(installer_path=installer_path, hashes=hashes)
            return

        print(f"WARNING: {installer_name} has no chunklist or hash, integrity NOT verified")


    def _validate_against_chunklist(self, installer_path: Path, chunklist: Path) -> bool:
        """
        Validate file against chunklist
        """
        name = Path(installer_path).name

        if not Path(installer_path).exists():
            print("File does not exist")
            return False

        if not Path(chunklist).exists():
            print("Chunklist does not exist")
            return False

        chunk_obj = integrity_verification.ChunklistVerification(installer_path, chunklist)
        if not chunk_obj.chunks:
            print("Failed to generate chunklist dict")
            self._remove_files([installer_path, chunklist])
            return False

        print(f"Validating {name} against chunklist: {chunk_obj.total_chunks} chunks", end="\r")
        chunk_obj.validate()

        while chunk_obj.status == integrity_verification.ChunklistStatus.IN_PROGRESS:
            print(f"Validating {name} against chunklist: chunk {chunk_obj.current_chunk} passed", end="\r")
            time.sleep(_POLL_INTERVAL)

        if chunk_obj.status != integrity_verification.ChunklistStatus.SUCCESS:
            print(chunk_obj.error_msg)
            print(f"Validating {name} against chunklist: chunk {chunk_obj.current_chunk} failed")
            self._remove_files([installer_path, chunklist])
            return False

        print(f"Validating {name} against chunklist: chunk {chunk_obj.total_chunks} passed")
        return True


    def _validate_against_hash(self, installer_path: Path, hashes: dict) -> bool:
        """
        Validate file against a hash published by AppleDB
        """
        name = Path(installer_path).name

        for key, algorithm in _HASH_ALGORITHMS:
            expected = hashes.get(key)
            if not isinstance(expected, str) or not expected:
                continue

            print(f"Validating {name} against {algorithm}")
            digest = hashlib.new(algorithm)
            with open(installer_path, "rb") as f:
                for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
                    digest.update(block)

            if digest.hexdigest().lower() != expected.strip().lower():
                print(f"Validating {name} against {algorithm}: FAILED")
                self._remove_files([installer_path])
                return False

            print(f"Validating {name} against {algorithm}: passed")
            return True

        print(f"WARNING: {name} has no supported hash, integrity NOT verified")
        return False


    def _remove_files(self, files: list) -> None:
        """
        Delete files, reporting failures
        """
        for file in files:
            try:
                Path(file).unlink(missing_ok=True)
            except OSError as e:
                print(f"Failed to delete {file}: {e}")


    def _downloader(self, url: str, path: Path, name: str) -> bool:
        """
        Download file from URL
        """
        dl_obj = network_handler.DownloadObject(url, path)
        dl_obj.download(display_progress=False, spawn_thread=True)

        last_percent = None
        while dl_obj.is_active():
            percent = int(dl_obj.get_percent())
            if percent != last_percent:
                last_percent = percent
                print(f"  Downloading: {name}: {dl_obj.get_percent():.2f}% ({utilities.human_fmt(dl_obj.get_speed())})/s", end="\r")
            time.sleep(_POLL_INTERVAL)

        print(f"  Downloading: {name}: 100.00% ({utilities.human_fmt(dl_obj.get_speed())})/s")

        if not dl_obj.download_complete:
            print("Download failed")
            self._remove_files([path]) # Retry later
            return False

        if Path(path).exists() and Path(path).stat().st_size == 0:
            print("Downloaded file is empty, considering permanent failure") # Likely dead URL
            dead_dir = Path(Path(path).parent, "Dead URLs")
            dead_dir.mkdir(exist_ok=True)
            target = Path(dead_dir, Path(path).name)
            if target.exists():
                self._remove_files([path])
            else:
                Path(path).rename(target)

        return True


    def _does_file_exist(self, xnu_version: int, build: str, suffix: str) -> bool:
        """
        Check if installer already exists in directory
        """
        if xnu_version not in self._os_table:
            raise ValueError(f"Unsupported OS version: {xnu_version}")

        if not Path(self._os_table[xnu_version]).exists():
            raise FileNotFoundError(f"Directory does not exist: {self._os_table[xnu_version]}")

        # Check failed, as those are generally dead URLs
        for path in [Path(self._os_table[xnu_version]), Path(self._os_table[xnu_version], "Dead URLs")]:
            if not Path(path).exists():
                continue
            for file in path.iterdir():
                if file.is_dir() or file.is_symlink():
                    continue
                if not file.name.endswith(suffix):
                    continue
                if f"({build})" in file.name:
                    return True

        return False


    def _get_remote_installer_catalog(self, os_version: int) -> dict:
        """
        Get remote installer catalog from Apple's servers
        """
        installers = {}
        print(f"SUCATALOG: Getting installers for macOS {os_data.os_conversion.kernel_to_os(os_version)}")
        for seed in macos_installer_handler.SeedType:
            print(f"  Catalog: {seed.name}")
            result = macos_installer_handler.RemoteInstallerCatalog(seed_override=seed, os_override=os_version).available_apps
            installers.update(result)

        return installers


    def _get_apple_db_items(self, variant: str = ".ipsw") -> dict:
        """
        Get macOS installers from AppleDB
        """
        if variant not in ["InstallAssistant.pkg", ".ipsw"]:
            raise ValueError(f"Invalid variant: {variant}")

        installers = {
            # "22F82": {
            #     url: "https://swcdn.apple.com/content/downloads/36/06/042-01917-A_B57IOY75IU/oocuh8ap7y8l8vhu6ria5aqk7edd262orj/InstallAssistant.pkg",
            #     version: "13.4.1",
            #     build: "22F82",
            # }
        }

        print(f"APPLEDB: Getting installers for variant: {variant}")

        apple_db = network_handler.NetworkUtilities().get("https://api.appledb.dev/main.json")
        if apple_db is None:
            return installers

        try:
            apple_db = apple_db.json()
        except ValueError:
            print("APPLEDB: Failed to parse response")
            return installers

        if not isinstance(apple_db, dict) or not isinstance(apple_db.get("ios"), list):
            print("APPLEDB: Unexpected response format")
            return installers

        for item in apple_db["ios"]:
            if not isinstance(item, dict):
                continue
            if item.get("osStr") != "macOS":
                continue
            if not isinstance(item.get("sources"), list):
                continue

            try:
                version = _safe_component(item["version"])
                build = _safe_build(item["build"])
                os_kernel = os_data.os_conversion.os_to_kernel(version.split(" ")[0])
            except (KeyError, UnsafeDataError, ValueError, TypeError, AttributeError) as e:
                print(f"APPLEDB: Skipping entry: {e}")
                continue

            if os_kernel not in self._os_table:
                continue

            for source in item["sources"]:
                if not isinstance(source, dict) or not isinstance(source.get("links"), list):
                    continue
                for entry in source["links"]:
                    if not isinstance(entry, dict) or not isinstance(entry.get("url"), str):
                        continue
                    if _url_suffix(entry["url"]) == "" or not urlparse(entry["url"]).path.endswith(variant):
                        continue

                    try:
                        url = _trusted_url(entry["url"])
                    except UnsafeDataError as e:
                        print(f"APPLEDB: Skipping {build}: {e}")
                        continue

                    models = []
                    for device in item.get("devices", []) or []:
                        if not isinstance(device, str):
                            continue
                        _device = device.split("-")[0]
                        if _device not in models:
                            models.append(_device)

                    hashes = source.get("hashes")
                    if not isinstance(hashes, dict):
                        hashes = {}

                    # Attempt to match macos_installer_handler.py's format
                    installers[build] = {
                        "Version":   version,
                        "Build":     build,
                        "Link":      url,
                        "Size":      -1,
                        "integrity": None,
                        "Hashes":    hashes,
                        "Source":    "AppleDB",
                        "Variant":   "Beta" if item.get("beta") else "Public",
                        "OS":        os_kernel,
                        "Models":    models,
                        "Date":      item.get("released"),
                    }

        return installers


    def _main(self) -> None:
        """
        Main entry point
        """
        installers = {}
        apple_db_ipsw_installers = {}
        apple_db_pkg_installers = {}

        for build in self._supported_oses:
            installers.update(self._get_remote_installer_catalog(os_version=build))

        for installer in [".ipsw", "InstallAssistant.pkg"]:
            apple_db_items = self._get_apple_db_items(variant=installer)
            if installer == ".ipsw":
                apple_db_ipsw_installers = apple_db_items
            else:
                apple_db_pkg_installers = apple_db_items
            installers.update(apple_db_items)

        # Drop anything with unexpected OS values or build numbers before touching the disk
        validated = {}
        for key, installer in installers.items():
            try:
                _safe_build(installer["Build"])
                if installer["OS"] not in self._os_table:
                    continue
            except (KeyError, UnsafeDataError) as e:
                print(f"Skipping installer: {e}")
                continue
            validated[key] = installer

        # Sort by name
        installers = dict(sorted(validated.items(), key=lambda item: item[1]["Build"]))

        print(f"Found {len(installers)} installers, checking which ones are missing")
        missing = []
        for build in installers:
            if self._does_file_exist(xnu_version=installers[build]["OS"], build=installers[build]["Build"], suffix=_url_suffix(installers[build]["Link"])) is True:
                continue
            missing.append(installers[build])

        print(f"Found {len(missing)} missing installers:" if missing else "No missing installers found")
        for installer in missing:
            print(f"  {_url_suffix(installer['Link'])}: {installer['Version']} ({installer['Build']})")
            self._download_installer(installer)

        # Finally, fix names
        for apple_db_installers in [apple_db_ipsw_installers, apple_db_pkg_installers]:
            for installer in apple_db_installers:
                _build = apple_db_installers[installer]["Build"]
                _version = apple_db_installers[installer]["Version"]
                if _version.lower().endswith(" beta"):
                    _version += " 1"
                elif " " not in _version:
                    _version += " release"

                try:
                    _base_name = _safe_component(f"{_version} ({_safe_build(_build)})")
                except UnsafeDataError as e:
                    print(f"Skipping rename: {e}")
                    continue

                for os in self._os_table:
                    for directory in [self._os_table[os], Path(self._os_table[os], "Dead URLs")]:
                        if not directory.exists():
                            continue
                        for file in directory.iterdir():
                            if file.is_dir() or file.is_symlink():
                                continue
                            if f"({_build})" not in file.name and f" {_build} " not in file.name:
                                continue

                            _name = _base_name
                            _current_suffix = Path(file).suffix
                            if _current_suffix == ".pkg":
                                _name += " InstallAssistant.pkg"
                            elif _current_suffix == ".ipsw":
                                _name += " Restore.ipsw"
                            elif _current_suffix == ".integrityDataV1":
                                if Path(file).name.endswith(" Restore.ipsw.integrityDataV1"):
                                    _name += " Restore.ipsw.integrityDataV1"
                                elif Path(file).name.endswith("InstallAssistant.pkg.integrityDataV1"):
                                    _name += " InstallAssistant.pkg.integrityDataV1"
                                else:
                                    continue
                            else:
                                continue

                            if Path(file).name == _name:
                                continue

                            try:
                                target = _inside(self._directory, Path(directory, _name))
                            except UnsafeDataError as e:
                                print(f"Skipping rename: {e}")
                                continue

                            if target.exists():
                                print(f"Refusing to rename {file.name}: {_name} already exists")
                                continue

                            print(f"Renaming {file.name} to {_name}")
                            try:
                                file.rename(target)
                            except OSError as e:
                                print(f"Failed to rename {file} to {target}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="macOS Installer Backup")
    parser.add_argument("--first-run", action="store_true", help="Create directories if missing")
    InstallerBackup(**vars(parser.parse_args()))
