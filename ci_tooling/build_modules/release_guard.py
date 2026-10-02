"""
release_guard.py: Compare the version in constants.py against the latest GitHub release

Build-Project.command runs this before the first build step, so a build that could never
become a usable release stops before any of the nine steps start.

    no release published yet      -> continue  (this build becomes the first release)
    local  >  remote              -> continue  (unreleased development version)
    local == remote, no assets    -> continue  (this build fills that release)
    local == remote, with assets  -> stop      (the release is already complete)
    local  <  remote, no assets   -> fix       (constants.py is bumped to the release tag)
    local  <  remote, with assets -> stop      (the tree is behind a finished release)

"local is ahead" has to continue: that is the normal state while a version is still being
worked on (constants.py at 4.0.0.190002, latest release 4.0.0.16903 with assets attached).
Treating it as a mismatch would block every ordinary build.

"stop" exits with code 3. Pass --ignore-release to Build-Project.command to build anyway.

Can also be run on its own, which is what the release workflow does:

    python3 -m ci_tooling.build_modules.release_guard --dry-run
"""

import os
import re
import sys
import json
import argparse
import urllib.error
import urllib.request

from pathlib import Path

import rich


EXIT_MIXED_UP:    int = 3
MIXED_UP_MESSAGE: str = "The app couldn't be generated because the version is mixed up"

DEFAULT_REPO:         str = "albert-mueller/OpenCore-Legacy-Patcher-T2"
DEFAULT_VERSION_FILE: str = "opencore_legacy_patcher/constants.py"
DEFAULT_VERSION_KEY:  str = "patcher_version"

API_URL: str = "https://api.github.com/repos/{repo}/releases/latest"


def parse_version(raw: str) -> tuple:
    """
    "4.0.0.190002", "v4.0.0", "4.0.0.190002s" -> tuple of ints

    Everything after the first non digit inside a component is dropped, so the trailing "s"
    that marks an unfinished version (see constants.py) does not break the comparison.
    """
    cleaned = raw.strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for chunk in cleaned.split("."):
        match = re.match(r"\d+", chunk)
        if match is None:
            break
        parts.append(int(match.group()))
    if not parts:
        raise ValueError(f"unparsable version: {raw!r}")
    return tuple(parts)


def compare(left: tuple, right: tuple) -> int:
    """-1, 0 or 1, padding the shorter tuple with zeros so 4.0.0 == 4.0.0.0"""
    length = max(len(left), len(right))
    left  = left  + (0,) * (length - len(left))
    right = right + (0,) * (length - len(right))
    return (left > right) - (left < right)


def version_pattern(key: str) -> "re.Pattern":
    # matches: self.patcher_version:  str = "4.0.0.190002"
    return re.compile(rf"({re.escape(key)}\s*(?::\s*[\w\[\], .]+?)?\s*=\s*[\"'])([^\"']+)([\"'])")


class ReleaseGuard:

    def __init__(self,
                 repo:           str = DEFAULT_REPO,
                 version_file:   str = DEFAULT_VERSION_FILE,
                 version_key:    str = DEFAULT_VERSION_KEY,
                 ignore_release: bool = False,
                 dry_run:        bool = False,
                 strict_network: bool = False,
                 token:          "str | None" = None,
                 ) -> None:
        self.repo           = repo
        self.version_file   = Path(version_file)
        self.version_key    = version_key
        self.ignore_release = ignore_release
        self.dry_run        = dry_run
        self.strict_network = strict_network
        self.token          = token or os.environ.get("GITHUB_TOKEN")


    def _read_local_version(self) -> str:
        match = version_pattern(self.version_key).search(self.version_file.read_text(encoding="utf-8"))
        if match is None:
            rich.print(f"[red]Error: no assignment for {self.version_key} found in {self.version_file}[/red]")
            sys.exit(EXIT_MIXED_UP)
        return match.group(2)


    def _write_local_version(self, new_version: str) -> None:
        text = self.version_file.read_text(encoding="utf-8")
        # Only the captured string literal is replaced, the surrounding alignment stays intact.
        patched, count = version_pattern(self.version_key).subn(
            lambda match: f"{match.group(1)}{new_version}{match.group(3)}", text, count=1
        )
        if count != 1:
            rich.print(f"[red]Error: could not rewrite {self.version_key} in {self.version_file}[/red]")
            sys.exit(EXIT_MIXED_UP)
        self.version_file.write_text(patched, encoding="utf-8")


    def _fetch_latest_release(self) -> "dict | None":
        """
        Latest published release, or None when the repository has none yet

        Drafts are not returned by this endpoint, so a release that is still being prepared
        locally never blocks a build.
        """
        request = urllib.request.Request(
            API_URL.format(repo=self.repo),
            headers={
                "Accept":               "application/vnd.github+json",
                "User-Agent":           "oclp-t2-release-guard",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            raise

        assets = [asset for asset in payload.get("assets", []) if asset.get("state", "uploaded") == "uploaded"]
        return {"tag": payload.get("tag_name", ""), "assets": assets}


    def _decide(self, local: tuple, remote: "tuple | None", has_assets: bool) -> tuple:
        if remote is None:
            return "continue", "no release published yet"

        order = compare(local, remote)

        if order > 0:
            return "continue", "the local version is ahead of the latest release"
        if order == 0:
            if has_assets:
                return "stop", "the latest release already has its assets uploaded"
            return "continue", "building the assets for the current release"
        if not has_assets:
            return "fix", "the local version is behind a release that has no assets yet"
        return "stop", "the local version is behind a release that is already complete"


    def check(self) -> str:
        """
        Run the check. Returns the version the build should carry, or exits with code 3.
        """
        local_raw = self._read_local_version()
        local     = parse_version(local_raw)

        try:
            release = self._fetch_latest_release()
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            rich.print(f"[yellow]Warning: could not reach GitHub ({error})[/yellow]")
            if self.strict_network:
                sys.exit(EXIT_MIXED_UP)
            rich.print(f"[yellow]         Skipping the release check, building {local_raw} as is.[/yellow]")
            _emit_output("action", "skipped")
            _emit_output("version", local_raw)
            return local_raw

        remote_raw = release["tag"] if release else None
        remote     = parse_version(remote_raw) if remote_raw else None
        has_assets = bool(release and release["assets"])

        asset_note = f", {len(release['assets'])} asset(s)" if release else ""
        rich.print(f"[yellow]Local version: {local_raw}, latest release: {remote_raw or 'none'}{asset_note}[/yellow]")

        action, reason = self._decide(local, remote, has_assets)

        if action == "stop" and self.ignore_release:
            rich.print(f"[yellow]{MIXED_UP_MESSAGE} - {reason}[/yellow]")
            rich.print("[yellow]--ignore-release was passed, building anyway.[/yellow]")
            action, reason = "continue", "forced by --ignore-release"

        if action == "stop":
            rich.print(f"[red]{MIXED_UP_MESSAGE} - {reason}[/red]")
            rich.print("[red]Pass --ignore-release to build anyway.[/red]")
            _emit_output("action", "stop")
            sys.exit(EXIT_MIXED_UP)

        if action == "fix":
            target = remote_raw.lstrip("vV")
            rich.print(f"[yellow]Fixing the version: {local_raw} -> {target} ({reason})[/yellow]")
            if self.dry_run is False:
                self._write_local_version(target)
            _emit_output("action", "fixed")
            _emit_output("version", target)
            return target

        _emit_output("action", "continue")
        _emit_output("version", local_raw)
        return local_raw


def _emit_output(name: str, value: str) -> None:
    """Hand the result to the workflow step that called this, if there is one."""
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the project version against the latest release")
    parser.add_argument("--repo",           type=str, default=os.environ.get("GITHUB_REPOSITORY") or DEFAULT_REPO)
    parser.add_argument("--version-file",   type=str, default=DEFAULT_VERSION_FILE)
    parser.add_argument("--version-key",    type=str, default=DEFAULT_VERSION_KEY)
    parser.add_argument("--ignore-release", action="store_true")
    parser.add_argument("--dry-run",        action="store_true", help="Report the decision without touching constants.py")
    parser.add_argument("--strict-network", action="store_true", help="Fail instead of continuing when GitHub is unreachable")
    args = parser.parse_args()

    ReleaseGuard(
        repo=args.repo,
        version_file=args.version_file,
        version_key=args.version_key,
        ignore_release=args.ignore_release,
        dry_run=args.dry_run,
        strict_network=args.strict_network,
    ).check()


if __name__ == "__main__":
    main()
