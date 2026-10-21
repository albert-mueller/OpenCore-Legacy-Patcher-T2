#!/usr/bin/env python3
"""
Build-Project.command: Generates OpenCore-Patcher-T2.app and OpenCore-Patcher-T2.pkg
"""

import os
import re
import sys
import time
import shutil
import argparse
import traceback
import subprocess
import threading

import rich
from rich.live import Live
from rich.spinner import Spinner
from pathlib import Path

# Fix: Force the execution directory immediately before importing local modules.
# This guarantees that 'ci_tooling' looks for assets in the right relative path.
SCRIPT_DIR = Path(__file__).resolve().parent
os.chdir(SCRIPT_DIR)


# OpenSSL 3 comes from MacPorts only. Homebrew is not used: it now only supports macOS
# on Apple Silicon, so it is unavailable on the Intel/T2 Macs this project is built on.
#
# MacPorts' openssl3 port keeps headers, libs and pkgconfig files together under
# libexec/openssl3 and symlinks the dylibs into /opt/local/lib.
MACPORTS_OPENSSL3_PREFIXES = [
    Path("/opt/local/libexec/openssl3"),
    Path("/opt/local"),
]
MACPORTS_PORT = "/opt/local/bin/port"


def _openssl3_prefix_is_valid(prefix: Path) -> bool:
    return (prefix / "lib" / "libssl.3.dylib").exists() and (prefix / "lib" / "libcrypto.3.dylib").exists()


def _find_port() -> "str | None":
    if Path(MACPORTS_PORT).exists():
        return MACPORTS_PORT
    return shutil.which("port")


def _print_failed_install(command: str, result: subprocess.CompletedProcess) -> None:
    rich.print(f"[red]Error: '{command}' failed with exit code {result.returncode}[/red]")
    print(result.stdout)
    print(result.stderr)


def _first_valid_prefix(prefixes: list) -> "Path | None":
    for prefix in prefixes:
        if _openssl3_prefix_is_valid(prefix):
            return prefix
    return None


def _install_openssl3_macports() -> Path:
    """
    Install MacPorts' openssl3 port

    MacPorts has to install as root. The order is:
    already root -> run directly; cached sudo credentials -> 'sudo -n'; otherwise ask for the
    admin password through a macOS authentication dialog. An interactive sudo password prompt
    is avoided on purpose: the rich Live spinner in the main thread would draw over it.
    """
    port = _find_port()
    if port is None:
        rich.print("[red]Error: OpenSSL 3 not found and MacPorts is not installed.[/red]")
        rich.print("[yellow]      Install MacPorts (https://www.macports.org/install.php), then run 'sudo port install openssl3'.[/yellow]")
        sys.exit(3)

    rich.print("[yellow]OpenSSL 3 not found, installing openssl3 via MacPorts...[/yellow]")
    install = [port, "-N", "install", "openssl3"]

    if os.geteuid() == 0:
        result = subprocess.run(install, capture_output=True, text=True)
    else:
        result = subprocess.run(["/usr/bin/sudo", "-n", *install], capture_output=True, text=True)
        if result.returncode != 0 and "password" in (result.stderr or "").lower():
            shell_command = " ".join(install)
            result = subprocess.run(
                ["/usr/bin/osascript", "-e",
                 f'do shell script "{shell_command}" with prompt "OpenCore-Patcher-T2 needs to install OpenSSL 3 via MacPorts." with administrator privileges'],
                capture_output=True, text=True,
            )

    if result.returncode != 0:
        _print_failed_install("port install openssl3", result)
        rich.print("[yellow]      Run 'sudo port install openssl3' manually, then build again.[/yellow]")
        sys.exit(3)

    prefix = _first_valid_prefix(MACPORTS_OPENSSL3_PREFIXES)
    if prefix is None:
        rich.print("[red]Error: openssl3 was installed, but libssl.3.dylib/libcrypto.3.dylib were not found under /opt/local.[/red]")
        sys.exit(3)
    return prefix


def ensure_openssl3(allow_install: bool = True) -> "Path | None":
    """
    Make sure OpenSSL 3 is present on the build machine, using MacPorts' openssl3 port

    'openssl version' is deliberately not used: on macOS it reports Apple's bundled LibreSSL,
    which says nothing about whether OpenSSL 3 is installed. The dylibs are checked instead.

    Returns the OpenSSL 3 prefix, or None when not building on macOS. Exits when OpenSSL 3
    is missing and cannot (or may not) be installed.
    """
    if sys.platform != "darwin":
        return None

    prefix = _first_valid_prefix(MACPORTS_OPENSSL3_PREFIXES)
    if prefix is not None:
        return prefix

    if allow_install is False:
        rich.print("[red]Error: OpenSSL 3 not found and --no-install-openssl was passed.[/red]")
        rich.print("[yellow]      Install it with 'sudo port install openssl3', then build again.[/yellow]")
        sys.exit(3)

    prefix = _install_openssl3_macports()
    rich.print(f"[green]Installed OpenSSL 3 at {prefix}[/green]")
    return prefix


def export_openssl3_environment(prefix: Path) -> None:
    """
    Point compilers, pkg-config and wheel builds run later in this process at OpenSSL 3
    """
    for key, value in {
        "LDFLAGS":         f"-L{prefix}/lib",
        "CPPFLAGS":        f"-I{prefix}/include",
        "PKG_CONFIG_PATH": f"{prefix}/lib/pkgconfig",
    }.items():
        existing = os.environ.get(key, "")
        if value not in existing.split(" " if key != "PKG_CONFIG_PATH" else ":"):
            separator = ":" if key == "PKG_CONFIG_PATH" else " "
            os.environ[key] = f"{value}{separator}{existing}" if existing else value
    os.environ["OPENSSL_DIR"] = str(prefix)


def verify_python_ssl() -> None:
    """
    The release guard talks to the GitHub API over HTTPS through urllib, which needs Python's
    ssl module - and depending on how this Python was built, that module links against
    OpenSSL 3. urllib decides once, at import time, whether HTTPS is available: when ssl fails
    to load there, urlopen() later raises URLError and the release guard silently skips the
    version check ("could not reach GitHub"). Load it here so that case is reported clearly.
    """
    try:
        import ssl
    except ImportError as e:
        rich.print(f"[red]Error: Python's ssl module failed to load: {e}[/red]")
        rich.print(f"[yellow]      OpenSSL 3 is installed, but {sys.executable} cannot use it.[/yellow]")
        rich.print("[yellow]      The version check against the latest release needs HTTPS.[/yellow]")
        sys.exit(3)


CA_BUNDLE_CANDIDATES = [
    Path("/opt/local/libexec/openssl3/etc/openssl/cert.pem"),    # MacPorts openssl3
    Path("/opt/local/share/curl/curl-ca-bundle.crt"),            # MacPorts curl-ca-bundle
    Path("/opt/local/etc/openssl/cert.pem"),                     # MacPorts
    Path("/etc/ssl/cert.pem"),                                   # macOS system bundle
]


def configure_ca_certificates() -> None:
    """
    Make sure Python's ssl module has root certificates to verify HTTPS against

    Pythons from python.org ship their own OpenSSL but no root certificates until
    "Install Certificates.command" was run once. Without them every HTTPS request fails with
    CERTIFICATE_VERIFY_FAILED ("unable to get local issuer certificate"), which made the
    release guard skip the version check. When the default context has no CA certificates,
    SSL_CERT_FILE is pointed at a known bundle: certifi's if installed, otherwise the one from
    MacPorts OpenSSL 3 or macOS itself. Set before any HTTPS context is created, so
    urllib (release guard) and every subprocess started by the build pick it up.
    An SSL_CERT_FILE the user set is left alone.
    """
    import ssl

    if os.environ.get("SSL_CERT_FILE"):
        return

    try:
        if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) > 0:
            return
    except ssl.SSLError:
        pass

    candidates = []
    try:
        import certifi
        candidates.append(Path(certifi.where()))
    except ImportError:
        pass
    candidates += CA_BUNDLE_CANDIDATES

    for bundle in candidates:
        if bundle.is_file() is False:
            continue
        os.environ["SSL_CERT_FILE"] = str(bundle)
        try:
            if ssl.create_default_context().cert_store_stats().get("x509_ca", 0) > 0:
                rich.print(f"[yellow]Note: this Python has no root certificates, using {bundle}[/yellow]")
                return
        except ssl.SSLError:
            pass
        del os.environ["SSL_CERT_FILE"]

    rich.print("[red]Error: Python's ssl module has no root certificates to verify HTTPS with.[/red]")
    rich.print("[yellow]      For a python.org Python, run 'Install Certificates.command' from its Applications folder,[/yellow]")
    rich.print("[yellow]      or set SSL_CERT_FILE to a CA bundle, then build again.[/yellow]")
    sys.exit(3)


# OpenSSL 3 is needed by the version check (release_guard), which runs before any build step.
# It is ensured here, BEFORE ci_tooling is imported: release_guard imports urllib.request, and
# urllib probes for ssl at that moment. Installing OpenSSL 3 only after that import - e.g. from
# inside main() - would leave urllib without HTTPS support for the rest of this process.
# This also runs before the rich Live spinner starts, so install output and the MacPorts
# admin password dialog are not drawn over.
if __name__ == "__main__" and not any(arg in ("-h", "--help") for arg in sys.argv[1:]):
    _openssl3_prefix = ensure_openssl3(allow_install="--no-install-openssl" not in sys.argv[1:])
    if _openssl3_prefix is not None:
        export_openssl3_environment(_openssl3_prefix)
        verify_python_ssl()
        configure_ca_certificates()


# Import der internen Module
from ci_tooling.build_modules import (
    application,
    disk_images,
    package,
    release_guard,
    sign_notarize,
    hash as hash_pkg
)


TOTAL_STEPS = 9

# Written by the build thread, read by the spinner in the main thread.
status = f"[0/{TOTAL_STEPS}] Starting"

# Filled in by _run_build(). "completed" is only True when main() returned normally,
# so the "Build script completed" line can never be printed after a failed build.
# Do NOT replace this with a module level flag assigned inside main(): an assignment
# there creates a function local unless 'global' is declared, and the module level
# value stays False forever - the failure mode this structure exists to prevent.
build_result = {"exit_code": 0, "completed": False}


def check_file_exists(path: Path) -> None:
    if not path.exists():
        rich.print(f"[red]Error: Expected file/directory not found: {path}[/red]")
        sys.exit(3)

def available_codesigning_identities() -> list:
    """
    Return (SHA-1 hash, name, status) for every valid code signing identity in the keychain

    'security find-identity -v' already filters out expired or otherwise unusable
    certificates. A self signed "Code Signing" certificate made in Keychain Access shows
    up here exactly like a Developer ID one.
    """
    if sys.platform != "darwin":
        return []

    try:
        result = subprocess.run(
            ["/usr/bin/security", "find-identity", "-v", "-p", "codesigning"],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        rich.print(f"[yellow]Warning: could not query signing identities: {e}[/yellow]")
        return []

    identities = []
    for line in result.stdout.splitlines():
        # "security find-identity" hängt bei nicht vertrauenswuerdigen Zertifikaten
        # eine Statusmeldung an, z.B. (CSSMERR_TP_NOT_TRUSTED). Ein selbst signiertes
        # Zertifikat ist damit trotzdem brauchbar: das Helper Tool vergleicht nur die
        # Zertifikatsketten und fuehrt keine Trust-Pruefung durch.
        #
        # "security find-identity" appends a status note for certificates that are not
        # trusted, e.g. (CSSMERR_TP_NOT_TRUSTED). A self signed certificate still works:
        # the helper tool only compares certificate chains and runs no trust evaluation.
        match = re.match(r'\s*\d+\)\s+([0-9A-Fa-f]{40})\s+"(.+?)"\s*(\(CSSMERR_[A-Z_]+\))?\s*$', line)
        if match:
            identities.append((match.group(1), match.group(2), match.group(3)))
    return identities


SIGNING_KEYCHAIN = Path.home() / "Library/Keychains/oclp-signing.keychain-db"


def lock_signing_keychain() -> None:
    """
    Lock the dedicated signing keychain (ci_tooling/privileged_helper_tool/create-signing-certificate.sh)
    again once signing is done, success or not.

    The privileged helper trusts exactly the certificate whose key lives there, so the key
    should only be usable while a build is signing. codesign unlocks it on demand (macOS
    asks for the keychain password); it also auto-locks after 5 minutes idle and on sleep.
    """
    if not SIGNING_KEYCHAIN.exists():
        return
    subprocess.run(["/usr/bin/security", "lock-keychain", str(SIGNING_KEYCHAIN)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def resolve_application_identity(requested: str, auto_detect: bool) -> "str | None":
    """
    Decide which identity the app and the privileged helper tool get signed with

    Both must end up carrying the same certificate: at runtime the helper requires its parent
    process to satisfy 'certificate leaf = H"<SHA-1 of the helper's own leaf>"' plus the app's
    identifier and the hardened runtime (ci_tooling/privileged_helper_tool/main.m), and refuses
    to run anything as root otherwise. It runs no trust evaluation, so a
    self signed certificate satisfies it just as well as a Developer ID one - but an unsigned
    or ad-hoc signed build carries no certificates at all and can never pass.
    """
    requested = requested or os.environ.get("MACOS_SIGNING_IDENTITY")
    identities = available_codesigning_identities()

    if requested:
        # Only reject when the lookup actually returned something; an empty list means the
        # query failed or we are not on macOS, which is not evidence the identity is missing.
        if identities and not any(requested in (identity_hash, name) for identity_hash, name, _ in identities):
            rich.print(f"[red]Error: no valid signing identity found matching: {requested}[/red]")
            available = ", ".join(f'"{name}"' for _, name, _ in identities) or "none"
            rich.print(f"[yellow]Available: {available}[/yellow]")
            sys.exit(3)
        return requested

    if auto_detect is False:
        return None

    if not identities:
        rich.print("[yellow]Note: no code signing certificate found, the app and helper tool stay unsigned.[/yellow]")
        rich.print("[yellow]      The privileged helper tool will refuse to run commands as root unless it was[/yellow]")
        rich.print("[yellow]      compiled with 'make debug' (ci_tooling/privileged_helper_tool/README.md).[/yellow]")
        rich.print("[yellow]      To sign locally, create a self signed certificate in Keychain Access:[/yellow]")
        rich.print("[yellow]      Certificate Assistant > Create a Certificate > Self Signed Root, type Code Signing.[/yellow]")
        return None

    if len(identities) > 1:
        rich.print("[yellow]Note: multiple code signing certificates found, none picked automatically:[/yellow]")
        for _, name, _ in identities:
            rich.print(f"[yellow]      - {name}[/yellow]")
        rich.print("[yellow]      Pass --application-signing-identity \"<name>\" to choose one.[/yellow]")
        return None

    _, name, identity_status = identities[0]
    rich.print(f"[yellow]Automatically selected signing identity: {name}[/yellow]")
    if identity_status:
        rich.print(f"[yellow]      Note: certificate is not trusted {identity_status} - that is enough for signing,[/yellow]")
        rich.print("[yellow]      the helper tool only compares certificate chains.[/yellow]")
    return name


def main() -> None:
    global status
    parser = argparse.ArgumentParser(description="Build OpenCore Legacy Patcher Suite")

    # Signing & Notarization
    parser.add_argument("--application-signing-identity", type=str, help="Application Signing Identity")
    parser.add_argument("--installer-signing-identity", type=str, help="Installer Signing Identity")
    parser.add_argument("--notarization-apple-id", type=str, help="Notarization Apple ID")
    parser.add_argument("--notarization-password", type=str, help="Notarization Password (Alternative: Env Var)")
    parser.add_argument("--notarization-team-id", type=str, help="Notarization Team ID")

    # CI/CD & Local Build Parameters
    parser.add_argument("--git-branch", type=str, default=None)
    parser.add_argument("--git-commit-url", type=str, default=None)
    parser.add_argument("--git-commit-date", type=str, default=None)
    parser.add_argument("--reset-dmg-cache", action="store_true")
    parser.add_argument("--reset-pyinstaller-cache", action="store_true")
    parser.add_argument("--no-auto-detect-identity", action="store_true", help="Never pick a signing identity from the keychain automatically")
    parser.add_argument("--ignore-release", action="store_true", help="Build even when the version does not line up with the latest release")
    parser.add_argument("--no-install-openssl", action="store_true", help="Fail instead of installing OpenSSL 3 via MacPorts when it is missing")
    parser.add_argument("--update-channel", type=str, default=None, choices=["official", "medelcartelinc"], help="Set the default update channel for this build")

    # Steps
    parser.add_argument("--run-as-individual-steps", action="store_true")
    parser.add_argument("--prepare-application", action="store_true")
    parser.add_argument("--prepare-package", action="store_true")
    parser.add_argument("--prepare-assets", action="store_true")

    args = parser.parse_args()

    # Passwort-Sicherheit: Umgebungsvariable hat Vorrang vor CLI-Argument
    notarization_password = os.environ.get("NOTARIZATION_PASSWORD") or args.notarization_password

    # The channel is embedded into the app's Info.plist by GenerateApplication.
    # An environment variable set here only lives as long as this build process,
    # the finished app never sees it - which is why the flag used to have no effect.
    if args.update_channel:
        rich.print(f"[cyan]Default update channel for this build: {args.update_channel}[/cyan]")

    # Resolved once, so the app and the helper tool can never end up signed with two different
    # certificates - which would make the helper reject the app at runtime.
    application_signing_identity = resolve_application_identity(
        args.application_signing_identity,
        auto_detect=args.no_auto_detect_identity is False,
    )

    # A build that could never become a usable release is stopped before the first step:
    # a version that is behind a release without assets is corrected, and a version that
    # would collide with a release that already has its assets is refused. --ignore-release
    # builds anyway.
    status = f"[0/{TOTAL_STEPS}] Checking the release state"
    release_guard.ReleaseGuard(ignore_release=args.ignore_release).check()

    try:
        # 1. Assets
        if (args.run_as_individual_steps is False) or (args.run_as_individual_steps and args.prepare_assets):
            status = f"[1/{TOTAL_STEPS}] Generating disk images"
            disk_images.GenerateDiskImages(args.reset_dmg_cache).generate()

        # 2. Application
        if (args.run_as_individual_steps is False) or (args.run_as_individual_steps and args.prepare_application):
            status = f"[2/{TOTAL_STEPS}] Signing Helper Tool"
            sign_notarize.SignAndNotarize(
                path=Path("./ci_tooling/privileged_helper_tool/com.albert-mueller.opencore-patcher-t2.privileged-helper"),
                signing_identity=application_signing_identity,
                notarization_apple_id=args.notarization_apple_id,
                notarization_password=notarization_password,
                notarization_team_id=args.notarization_team_id,
            ).sign_and_notarize()

            status = f"[3/{TOTAL_STEPS}] Building the app"
            application.GenerateApplication(
                reset_pyinstaller_cache=args.reset_pyinstaller_cache,
                git_branch=args.git_branch,
                git_commit_url=args.git_commit_url,
                git_commit_date=args.git_commit_date,
                update_channel=args.update_channel,
            ).generate()

            check_file_exists(Path("dist/OpenCore-Patcher-T2.app"))
            status = f"[4/{TOTAL_STEPS}] Signing the app"
            sign_notarize.SignAndNotarize(
                path=Path("dist/OpenCore-Patcher-T2.app"),
                signing_identity=application_signing_identity,
                notarization_apple_id=args.notarization_apple_id,
                notarization_password=notarization_password,
                notarization_team_id=args.notarization_team_id,
                entitlements=Path("./ci_tooling/entitlements/entitlements.plist"),
            ).sign_and_notarize()

        # 3. Packages
        if (args.run_as_individual_steps is False) or (args.run_as_individual_steps and args.prepare_package):
            status = f"[5/{TOTAL_STEPS}] Building packages"
            package.GeneratePackage().generate()

            # AutoPkg-Assets-T2.pkg is installed by the app itself during auto patching,
            # so it needs a signature just as much as the two user facing packages.
            step = 6
            for pkg in ["OpenCore-Patcher-T2.pkg", "OpenCore-Patcher-Uninstaller.pkg", "AutoPkg-Assets-T2.pkg"]:
                pkg_path = Path(f"dist/{pkg}")
                check_file_exists(pkg_path)
                status = f"[{step}/{TOTAL_STEPS}] Signing {pkg}"
                sign_notarize.SignAndNotarize(
                    path=pkg_path,
                    signing_identity=args.installer_signing_identity,
                    notarization_apple_id=args.notarization_apple_id,
                    notarization_password=notarization_password,
                    notarization_team_id=args.notarization_team_id,
                ).sign_and_notarize()
                step += 1

            status = f"[{TOTAL_STEPS}/{TOTAL_STEPS}] Generating hashes"
            hash_pkg.GenerateHash()
    except Exception as e:
        # Closing tag must match the opening one - rich raises MarkupError on a mismatch,
        # which would replace the real build error with a markup error.
        rich.print(f"\n[red] Building the app stopped because of some error: {e}[/red]")
        # Print the traceback too. Without it the message alone gives no file or line,
        # which turns any error raised deep in a build module into a repo-wide hunt.
        traceback.print_exc()
        sys.exit(3)
    finally:
        lock_signing_keychain()


def _run_build() -> None:
    """
    Thread entry point

    sys.exit() inside a thread only unwinds that thread, it does not set the process
    exit code. Every failure is recorded here instead, so __main__ can exit non-zero
    and CI actually fails on a broken build.
    """
    try:
        main()
    except SystemExit as e:
        build_result["exit_code"] = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except BaseException:
        traceback.print_exc()
        build_result["exit_code"] = 1
    else:
        build_result["completed"] = True


if __name__ == '__main__':
    _start = time.time()

    thread = threading.Thread(target=_run_build)
    thread.start()

    spinner = Spinner("dots", text=status)
    with Live(spinner, refresh_per_second=10):
        while thread.is_alive():
            spinner.update(text=status)
            time.sleep(0.1)
        spinner.update(text=status)

    thread.join()

    if build_result["exit_code"] != 0:
        sys.exit(build_result["exit_code"])

    if build_result["completed"] is False:
        # main() left early without failing, e.g. argparse handled --help.
        sys.exit(0)
    else: # behebt einen Fehler, indem es ohne Bedingung Build script completed ausdruckt; diesmal nicht richtig eine Sicherheitslücke
        rich.print(f"\n[green]Build script completed in {str(round(time.time() - _start, 2))} seconds.[/green]")
