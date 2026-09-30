"""
disk_images.py: Fetch and generate disk images (Universal-Binaries.dmg, payloads.dmg)
"""

import os
import subprocess
import rich
from rich.progress import Progress, BarColumn, TextColumn, TimeRemainingColumn
from pathlib import Path

from opencore_legacy_patcher import constants
from opencore_legacy_patcher.support import subprocess_wrapper

class GenerateDiskImages:

    # Smallest size (bytes) a downloaded resource can plausibly have.
    # Anything below this is an error page or a truncated transfer, not a disk image.
    MINIMUM_RESOURCE_SIZE: int = 1024 * 1024  # 1 MB

    def __init__(self, reset_dmg_cache: bool = False) -> None:
        """
        Initialize
        """
        self.reset_dmg_cache = reset_dmg_cache

    def _detach_mounts_under(self, path: Path) -> None:
        """
        Detach any volume mounted at or below `path`.

        If a previous build (or a crashed/interrupted run) left a disk image such as
        Universal-Binaries.dmg attached inside payloads/, `rm -rf` fails with
        "Resource busy" because macOS refuses to remove an active mount point.
        """
        target = str(path.resolve())
        result = subprocess.run(["/sbin/mount"], capture_output=True, text=True)

        mount_points = []
        for line in result.stdout.splitlines():
            # Format: /dev/disk4s1 on /path/to/mount (apfs, local, nodev, ...)
            if " on " not in line or " (" not in line:
                continue
            mount_point = line.split(" on ", 1)[1].rsplit(" (", 1)[0]
            if mount_point == target or mount_point.startswith(target + os.sep):
                mount_points.append(mount_point)

        # Fallback in case `mount` output could not be parsed
        if not mount_points and os.path.ismount(target):
            mount_points.append(target)

        # Deepest mounts first, so nested volumes are released before their parents
        for mount_point in sorted(set(mount_points), key=len, reverse=True):
            rich.print(f"  - Detaching volume mounted at {mount_point}")
            detach = subprocess.run(
                ["/usr/bin/hdiutil", "detach", mount_point],
                capture_output=True, text=True
            )
            if detach.returncode == 0:
                continue

            # Finder/Spotlight may still hold the volume open, force it
            detach = subprocess.run(
                ["/usr/bin/hdiutil", "detach", mount_point, "-force"],
                capture_output=True, text=True
            )
            if detach.returncode != 0:
                raise Exception(
                    f"Failed to detach {mount_point}, cannot delete {path}: "
                    f"{(detach.stderr or detach.stdout).strip()}\n"
                    f"Detach it manually with: sudo hdiutil detach \"{mount_point}\" -force"
                )

    def _delete_extra_binaries(self):
        """
        Delete extra binaries from payloads directory
        """
        whitelist_folders = [
            "ACPI",
            "Config",
            "Drivers",
            "Icon",
            "Kexts",
            "OpenCore",
            "Tools",
            "Launch Services",
            "Resources",  # Preserve Resources directory so PyInstaller can include icons/assets
        ]

        whitelist_files = []

        rich.print("Deleting extra binaries...")
        for file in Path("payloads").glob(pattern="*"):
            if file.is_dir():
                if file.name in whitelist_folders:
                    continue
                rich.print(f"- Deleting {file.name}")
                self._detach_mounts_under(file)
                subprocess_wrapper.run_and_verify(["/bin/rm", "-rf", file])
            else:
                if file.name in whitelist_files:
                    continue
                rich.print(f"- Deleting {file.name}")
                subprocess_wrapper.run_and_verify(["/bin/rm", "-f", file])

    def _generate_payloads_dmg(self):
        """
        Generate disk image containing all payloads
        Disk image will be password protected due to issues with
        Apple's notarization system and inclusion of kernel extensions
        """

        if Path("./payloads.dmg").exists():
            if self.reset_dmg_cache is False:
                rich.print("- payloads.dmg already exists, skipping creation")
                return

            rich.print("- Removing old payloads.dmg")
            subprocess_wrapper.run_and_verify(
                ["/bin/rm", "-rf", "./payloads.dmg"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )

        rich.print("Generating DMG...")

        # Fixed: Using -stdinpass to avoid deprecated/insecure -passphrase
        cmd = [
            '/usr/bin/hdiutil', 'create', './payloads.dmg',
            '-megabytes', '32000',
            '-format', 'UDZO', '-ov',
            '-volname', 'OpenCore Patcher Resources (Base)',
            '-fs', 'APFS',
            '-layout', 'NONE',
            '-srcfolder', './payloads'
        ]

        # Use Popen to pipe the password securely
        process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        stdout, stderr = process.communicate(input=b"password\n")

        if process.returncode != 0:
            raise Exception(f"Failed to generate DMG: {stderr.decode().strip()}")

        rich.print("[green]DMG generation complete[/green]")

    def _download_resources(self):
        """
        Download required dependencies
        """

        oclp_constants = constants.Constants()
        patcher_support_pkg_version = oclp_constants.patcher_support_pkg_version
        base_url = oclp_constants.url_patcher_support_pkg.rstrip("/")
        required_resources = [
            "Universal-Binaries.dmg"
        ]

        rich.print("Downloading required resources...")
        for resource in required_resources:
            resource_path = Path(f"./{resource}")

            if resource_path.exists():
                if self.reset_dmg_cache is True:
                    rich.print(f"  - Removing old {resource}")
                    assert resource, "Resource cannot be empty"
                    assert resource not in ("/", "."), "Resource cannot be root"
                    subprocess_wrapper.run_and_verify(
                        ["/bin/rm", "-rf", f"./{resource}"],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE
                    )
                elif resource_path.stat().st_size < self.MINIMUM_RESOURCE_SIZE:
                    # A previous run may have cached an HTTP error page under this name.
                    # Never trust such a file, redownload instead of skipping.
                    rich.print(f"  - Existing {resource} is only {resource_path.stat().st_size} bytes, discarding and redownloading")
                    resource_path.unlink()
                else:
                    rich.print(f"- {resource} already exists, skipping download")
                    continue

            url = f"{base_url}/{patcher_support_pkg_version}/{resource}"
            rich.print(f"- Fetching {url}")

            process = subprocess.Popen(
                [
                    "curl",
                    "-L",
                    "--fail",       # Exit non-zero on HTTP errors instead of writing the error page to disk
                    "--progress-bar",
                    "-o", f"./{resource}",
                    url,
                ],
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )

            error_output = []

            with Progress(
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                TextColumn("{task.percentage:>3.0f}%"),
                TimeRemainingColumn(),
            ) as progress:

                task = progress.add_task(f"Downloading {resource}...", total=100)

                # curl writes its progress bar to stderr.
                for line in process.stderr:
                    # curl's progress bar uses carriage returns.
                    # Extract the percentage from the output.
                    line = line.strip()

                    if not line:
                        continue

                    if line.endswith("%"):
                        try:
                            percent = float(line.split()[-1].rstrip("%"))
                            progress.update(task, completed=percent)
                            continue
                        except ValueError:
                            pass

                    # Anything that is not progress output is diagnostic, keep it for the error message
                    error_output.append(line)

            process.wait()

            if process.returncode != 0:
                # Remove whatever curl left behind, otherwise the next run caches a broken file
                resource_path.unlink(missing_ok=True)
                detail = " ".join(error_output[-3:]) or f"curl exited with code {process.returncode}"
                rich.print(f"[bold red]Failed to download {resource}[/bold red]")
                raise Exception(
                    f"Failed to download {resource} from {url}: {detail}\n"
                    f"Verify that release tag '{patcher_support_pkg_version}' exists and publishes {resource}."
                )

            if not resource_path.exists():
                rich.print(f"[bold red] {resource} not found[/bold red]")
                raise Exception(f"{resource} not found")

            resource_size = resource_path.stat().st_size
            if resource_size < self.MINIMUM_RESOURCE_SIZE:
                resource_path.unlink(missing_ok=True)
                raise Exception(
                    f"{resource} downloaded from {url} is only {resource_size} bytes, this is not a valid disk image.\n"
                    f"Verify that release tag '{patcher_support_pkg_version}' exists and publishes {resource}."
                )

            rich.print(f"[green]- Downloaded {resource} ({resource_size / (1024 * 1024):.1f} MB)[/green]")

    def generate(self) -> None:
        """
        Generate disk images
        """
        self._delete_extra_binaries()
        self._generate_payloads_dmg()
        self._download_resources()
