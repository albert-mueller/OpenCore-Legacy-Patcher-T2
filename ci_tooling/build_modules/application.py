import sys
import time
import shutil
import plistlib
import subprocess
import rich
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
            shutil.rmtree(self._application_output)

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
            rich.print("[yellow]Analytics key or endpoint not provided, skipping embedding[/yellow]")
            return

        self._update_analytics_source(self._analytics_key, self._analytics_endpoint)


    def _remove_analytics_key(self) -> None:
        """
        Remove analytics key safely from the script
        """
        if all([self._analytics_key, self._analytics_endpoint]):
            self._update_analytics_source("", "")


    def _patch_load_command(self) -> None:
        """
        Patch LC_VERSION_MIN_MACOSX in Load Command to report 10.13.6
        """
        _file = self._application_output / "Contents" / "MacOS" / "OpenCore-Patcher-T2"

        _find    = b'\x00\x0D\x0A\x00' # 10.13
        _replace = b'\x06\x0D\x0A\x00' # 10.13.6

        if not _file.exists():
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
        _file = self._application_output / "Contents" / "MacOS" / "OpenCore-Patcher-T2"

        _find    = b'\x00\x01\x0C\x00'
        _replace = b'\x00\x00\x1A\x00'

        if not _file.exists():
            raise FileNotFoundError(f"Target binary not found for patching: {_file}")

        with open(_file, "rb") as f:
            data = f.read()

        # Bounded to the first match, like _patch_load_command() above. The load command
        # lives once in the Mach-O header at the front of the file, but this is a 4-byte
        # sequence that recurs by chance across a multi-megabyte binary full of embedded
        # bytecode - an unbounded replace() silently rewrote those unrelated hits too.
        data = data.replace(_find, _replace, 1)

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
            rich.print("[yellow]Warning: 'git' executable not found, skipping local commit metadata[/yellow]")
            return ""
        except subprocess.CalledProcessError as e:
            _stderr = (e.stderr or "").strip()
            if "not a git repository" in _stderr.lower():
                rich.print("[yellow]Warning: this checkout has no .git directory (likely downloaded as a ZIP/tarball instead of via 'git clone') - Commit Information will show N/A. Use 'git clone' (or 'git pull')[/yellow]")
            else:
                rich.print(f"[yellow]Warning: 'git {' '.join(args)}' failed: {_stderr or e}[/yellow]")
            return ""
        except Exception as e:
            rich.print(f"[yellow]Warning: 'git {' '.join(args)}' failed: {e}[/yellow]")
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


        if not _file.exists():
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
        resources_dir = self._application_output / "Contents" / "Resources"
        resources_dir.mkdir(parents=True, exist_ok=True)

        app_icons_dir = Path("payloads/Resources/AppIcons")
        if not app_icons_dir.is_dir():
            raise FileNotFoundError(f"AppIcons directory not found: {app_icons_dir}")

        # Iterate the directory's entries - a Path is not itself iterable, which is
        # where "'PosixPath' object is not iterable" came from.
        #
        # Copy everything in here rather than filtering on *.icns. OC-Patcher.png
        # backs constants.app_icon_path_png, which embed_readme() rewrites the
        # README's image URL to point at, and Assets.car is what hands macOS the app
        # icon the same way Apple's own apps do. An extension filter silently drops
        # both and the failure only shows up at runtime.
        for file in sorted(app_icons_dir.iterdir()):
            if file.name.startswith("."):
                continue
            subprocess_wrapper.run_and_verify(
                generate_copy_arguments(str(file), str(resources_dir)),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )




    def embed_readme(self) -> str:
        """
        Embed README into the about frame logic for the application build.
        """
        repository_root = Path(__file__).resolve().parents[2]
        readme_file = repository_root / "README.md"
        about_file_path = repository_root / "opencore_legacy_patcher" / "wx_gui" / "gui_about.py"
        if not readme_file.exists():
            raise FileNotFoundError(f"README.md not found: {readme_file}")
        if not about_file_path.exists():
            raise FileNotFoundError(f"About frame source not found: {about_file_path}")

        with open(readme_file, "r", encoding="utf-8") as f:
            readme_text = f.read()
        with open(about_file_path, "r", encoding="utf-8") as f:
            about_file = f.read()

        image_url = "https://raw.githubusercontent.com/dortania/OpenCore-Legacy-Patcher/macos-next/docs/images/OC-Patcher.png"
        source_line = "markdown_text = Path(\"./README.md\").read_text(encoding=\"utf-8\")"
        replacement = (
            f"markdown_text = {repr(readme_text)}\n"
            f"        markdown_text = markdown_text.replace({image_url!r}, "
            "Path(self.constants.app_icon_path_png).resolve().as_uri())"
        )
        if source_line not in about_file:
            raise RuntimeError("README loading statement not found in gui_about.py")

        modified_about = about_file.replace(source_line, replacement, 1)
        if "import wx.html2" not in modified_about:
            modified_about = modified_about.replace("import wx\n", "import wx\nimport wx.html2\n", 1)

        source_base_url = 'base_url = Path("./README.md").resolve().parent.as_uri() + "/"'
        packaged_base_url = 'base_url = Path(self.constants.app_icon_path_png).resolve().parent.as_uri() + "/"'
        if source_base_url in modified_about:
            modified_about = modified_about.replace(source_base_url, packaged_base_url, 1)
        elif packaged_base_url not in modified_about:
            marker = "        self.webview.SetPage(html_document)"
            if marker in modified_about:
                modified_about = modified_about.replace(
                    marker,
                    f"        {packaged_base_url}\n\n{marker}",
                    1,
                )

        modified_about = modified_about.replace(
            "self.webview.SetPage(html_document)",
            "self.webview.SetPage(html_document, base_url)",
            1,
        )

        with open(about_file_path, "w", encoding="utf-8") as f:
            f.write(modified_about)

        return about_file

    def remove_hard_readme(self, about_file: str) -> None:
        """
        Remove hardcoded README from the about frame logic
        """
        repository_root = Path(__file__).resolve().parents[2]
        about_file_path = repository_root / "opencore_legacy_patcher" / "wx_gui" / "gui_about.py"
        with open(about_file_path, "w", encoding="utf-8") as f:
            f.write(about_file)

    def generate(self) -> None:
        """
        Generate OpenCore-Patcher-T2.app
        """
        about_file = None
        try:
            self._embed_analytics_key()
            about_file = self.embed_readme()
            self._generate_application()
        finally:
            # Always sanitizes the local source code file even if the build crashes
            self._remove_analytics_key()
            if about_file is not None:
                self.remove_hard_readme(about_file=about_file)

        self._patch_load_command()

        if not self._git_branch or not self._git_branch.startswith('refs/tags'):
            self._patch_sdk_version()

        self._embed_git_data()
        self._embed_resources()