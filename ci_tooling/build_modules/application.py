import sys
import time
import shutil
import plistlib
import subprocess
from pathlib import Path

from opencore_legacy_patcher.volume import generate_copy_arguments
from opencore_legacy_patcher.support import subprocess_wrapper


class GenerateApplication:
    """
    Generate OpenCore-Patcher-T2.app
    """

    def __init__(self, reset_pyinstaller_cache: bool = False, git_branch: str = None, 
                 git_commit_url: str = None, git_commit_date: str = None, 
                 analytics_key: str = None, analytics_endpoint: str = None) -> None:
        """
        Initialize
        """
        self._pyinstaller = [sys.executable, "-m", "PyInstaller"]
        self._application_output = Path("./dist/OpenCore-Patcher-T2.app")

        self._reset_pyinstaller_cache = reset_pyinstaller_cache

        self._git_branch = git_branch
        self._git_commit_url = git_commit_url
        self._git_commit_date = git_commit_date

        self._analytics_key = analytics_key
        self._analytics_endpoint = analytics_endpoint
        
        # Back to your original target file path
        self._analytics_source_file = Path("./opencore_legacy_patcher/support/analytics_handler.py")


    def _generate_application(self) -> None:
        """
        Generate PyInstaller Application
        """
        if self._application_output.exists():
            print(f"Cleaning existing build: {self._application_output}")
            shutil.rmtree(self._application_output)

        print("Generating OpenCore-Patcher-T2.app")
        _args = self._pyinstaller + ["./OpenCore-Patcher-GUI.spec", "--noconfirm"]
        if self._reset_pyinstaller_cache:
            _args.append("--clean")

        subprocess_wrapper.run_and_verify(_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


    def _update_analytics_source(self, key: str, endpoint: str) -> None:
        """
        Safely writes specific variables into the analytics source code.
        Uses Python representation format to eliminate code injection threats.
        """
        if not self._analytics_source_file.exists():
            raise FileNotFoundError(f"Source file not found: {self._analytics_source_file}")

        with open(self._analytics_source_file, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # repr() automatically wraps the string in quotes and safely escapes 
        # hazardous characters (like internal quotes, newlines, or backslashes)
        safe_key = repr(key or "")
        safe_endpoint = repr(endpoint or "")

        for i, line in enumerate(lines):
            if line.startswith("SITE_KEY:         str = "):
                lines[i] = f"SITE_KEY:         str = {safe_key}\n"
            elif line.startswith("ANALYTICS_SERVER: str = "):
                lines[i] = f"ANALYTICS_SERVER: str = {safe_endpoint}\n"

        with open(self._analytics_source_file, "w", encoding="utf-8") as f:
            f.writelines(lines)


    def _embed_analytics_key(self) -> None:
        """
        Embed analytics key safely into the script
        """
        if not all([self._analytics_key, self._analytics_endpoint]):
            print("Analyseschlüssel oder Endpunkt nicht angegeben, Einbettung wird übersprungen")
            print("Analytics key or endpoint not provided, skipping embedding")
            return

        print("Embedding analytics data safely into source file")
        self._update_analytics_source(self._analytics_key, self._analytics_endpoint)


    def _remove_analytics_key(self) -> None:
        """
        Remove analytics key safely from the script
        """
        if all([self._analytics_key, self._analytics_endpoint]):
            print("Wiping analytics data from source file")
            self._update_analytics_source("", "")


    def _patch_load_command(self) -> None:
        """
        Patch LC_VERSION_MIN_MACOSX in Load Command to report 10.10
        """
        _file = self._application_output / "Contents" / "MacOS" / "OpenCore-Patcher"

        _find    = b'\x00\x0D\x0A\x00' # 10.13
        _replace = b'\x00\x0A\x0A\x00' # 10.10

        print("Patching LC_VERSION_MIN_MACOSX")
        if not _file.exists():
            raise FileNotFoundError(f"Zieldatei zum Patchen nicht gefunden: {_file}")
            raise FileNotFoundError(f"Target binary not found for patching: {_file}")

        with open(_file, "rb") as f:
            data = f.read()
            
        data = data.replace(_find, _replace, 1)

        with open(_file, "wb") as f:
            f.write(data)


    def _patch_sdk_version(self) -> None:
        """
        Patch LC_BUILD_VERSION in Load Command to report the macOS 26 SDK
        """
        _file = self._application_output / "Contents" / "MacOS" / "OpenCore-Patcher"

        _find    = b'\x00\x01\x0C\x00'
        _replace = b'\x00\x00\x1A\x00'

        print("Patching LC_BUILD_VERSION")
        if not _file.exists():
            raise FileNotFoundError(f"Zieldatei zum Patchen nicht gefunden: {_file}")
            raise FileNotFoundError(f"Target binary not found for patching: {_file}")

        with open(_file, "rb") as f:
            data = f.read()
            
        data = data.replace(_find, _replace)

        with open(_file, "wb") as f:
            f.write(data)


    def _run_git(self, args: list) -> str:
        """
        Best-effort git invocation, returns "" on any failure
        (eg. not a git checkout, git missing, detached worktree, etc.)
        Logs *why* it failed instead of failing silently, since a swallowed
        failure here is exactly what makes "Commit URL: N/A" confusing to
        debug from the Settings panel alone.
        """
        try:
            result = subprocess.run(
                ["git"] + args, capture_output=True, text=True, check=True
            )
            return result.stdout.strip()
        except FileNotFoundError:
            print("Warning: 'git' executable not found, skipping local commit metadata")
            return ""
        except subprocess.CalledProcessError as e:
            _stderr = (e.stderr or "").strip()
            if "not a git repository" in _stderr.lower():
                print("Warning: this checkout has no .git directory (likely downloaded as a ZIP/tarball instead of via 'git clone') - Commit Information will show N/A. Use 'git clone' (or 'git pu[...]")
            else:
                print(f"Warning: 'git {' '.join(args)}' failed: {_stderr or e}")
            return ""
        except Exception as e:
            print(f"Warning: 'git {' '.join(args)}' failed: {e}")
            return ""


    def _derive_local_git_metadata(self) -> "tuple[str, str, str]":
        """
        CI passes --git-branch/--git-commit-url/--git-commit-date explicitly.
        Local/manual builds (just running Build-Project.command directly)
        don't, which left "Commit URL" as an empty string and the Settings
        UI showing "N/A" for it even inside a real git checkout. Fall back
        to asking the local repo directly so manual builds still get real,
        clickable commit info.
        """
        branch      = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        commit_hash = self._run_git(["rev-parse", "HEAD"])
        commit_date = self._run_git(["log", "-1", "--format=%cd", "--date=format:%Y-%m-%d %H:%M:%S"])
        remote_url  = self._run_git(["config", "--get", "remote.origin.url"])

        commit_url = ""
        if remote_url and commit_hash:
            if remote_url.endswith(".git"):
                remote_url = remote_url[:-len(".git")]
            if remote_url.startswith("git@"):
                # git@github.com:user/repo -> https://github.com/user/repo
                remote_url = "https://" + remote_url[len("git@"):].replace(":", "/", 1)
            commit_url = f"{remote_url}/commit/{commit_hash}"

        return branch, commit_url, commit_date


    def _embed_git_data(self) -> None:
        """
        Embed git data
        """
        _file = self._application_output / "Contents" / "Info.plist"

        _local_branch, _local_commit_url, _local_commit_date = self._derive_local_git_metadata()

        _git_branch = self._git_branch or _local_branch or "Built from source"
        _git_commit = self._git_commit_url or _local_commit_url or ""
        _git_commit_date = self._git_commit_date or _local_commit_date or time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        if not _git_commit:
            print("Warning: could not determine a Commit URL (no --git-commit-url passed and no local git metadata found) - Settings will show N/A for it")

        print("Embedding git data")
        if not _file.exists():
            raise FileNotFoundError(f"Info.plist nicht gefunden: {_file}")
            raise FileNotFoundError(f"Info.plist not found: {_file}")

        with open(_file, "rb") as f:
            _plist = plistlib.load(f)

        _plist["Github"] = {
            "Branch": _git_branch,
            "Commit URL": _git_commit,
            "Commit Date": _git_commit_date
        }

        with open(_file, "wb") as f:
            plistlib.dump(_plist, f, sort_keys=True)


    def _embed_resources(self) -> None:
        """
        Embed resources
        """
        print("Embedding resources")
        resources_dir = self._application_output / "Contents" / "Resources"
        resources_dir.mkdir(parents=True, exist_ok=True)

        for file in Path("payloads/Resources/AppIcons").glob("*.icns"):
            subprocess_wrapper.run_and_verify(
                generate_copy_arguments(str(file), resources_dir / ""),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )


    def generate(self) -> None:
        """
        Generate OpenCore-Patcher-T2.app
        """
        try:
            self._embed_analytics_key()
            self._generate_application()
        finally:
            # Always sanitizes the local source code file even if the build crashes
            self._remove_analytics_key()

        self._patch_load_command()
        
        if not self._git_branch or not self._git_branch.startswith('refs/tags'):
            self._patch_sdk_version()

        self._embed_git_data()
        self._embed_resources()
        
        print("Build-Generierung abgeschlossen.")
        print("Build generation complete.")
