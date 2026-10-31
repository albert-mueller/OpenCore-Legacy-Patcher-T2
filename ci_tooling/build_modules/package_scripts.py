"""
package_scripts.py: Generate pre/postinstall scripts for PKG and AutoPkg
"""


from opencore_legacy_patcher import constants

LEGACY_DOMAIN = "com.dortania.opencore-legacy-patcher"
NEW_DOMAIN    = "com.albert-mueller.opencore-legacy-patcher"

# Install locations, relative to the target volume
_INSTALL_DIRECTORY   = constants.APP_INSTALL_DIRECTORY.lstrip("/")
MAIN_APP_PATH        = f"{_INSTALL_DIRECTORY}/{constants.APP_BUNDLE_NAME}"
SHIM_APP_PATH        = f"Applications/{constants.APP_BUNDLE_NAME}"
EXECUTABLE_PATH      = f"{MAIN_APP_PATH}/Contents/MacOS/{constants.APP_EXECUTABLE_NAME}"

# Same locations under the bundle name used before the rename to BridgePatcher
LEGACY_MAIN_APP_PATH   = f"{_INSTALL_DIRECTORY}/{constants.LEGACY_APP_BUNDLE_NAME}"
LEGACY_SHIM_APP_PATH   = f"Applications/{constants.LEGACY_APP_BUNDLE_NAME}"
LEGACY_EXECUTABLE_PATH = f"{LEGACY_MAIN_APP_PATH}/Contents/MacOS/{constants.LEGACY_APP_EXECUTABLE_NAME}"


_VERIFY_BODY = '    local packageHash=""\n    if [[ -f $pathToPackage ]]; then\n        packageHash=$(/usr/bin/shasum -a 256 "$pathToPackage" | /usr/bin/awk \'{print $1}\')\n        echo "Package SHA256: $packageHash"\n    else\n        echo "Package file not found ($pathToPackage), can\'t compute its checksum"\n    fi\n\n    local url list reached=0\n    for url in $checksumUrls; do\n        list=$(/usr/bin/curl --fail --silent --location --max-time 20 "$url" 2>/dev/null) || continue\n        reached=1\n        if [[ -n $packageHash ]] && print -r -- "$list" | /usr/bin/grep -qiwF -- "$packageHash"; then\n            echo "Verified: checksum matches $url"\n            return 0\n        fi\n    done\n\n    local reason\n    if [[ -z $packageHash ]]; then\n        reason="The installer couldn\'t compute this package\'s checksum."\n    elif (( reached )); then\n        reason="Its SHA256 checksum doesn\'t match any checksum published for version @VERSION@."\n    else\n        reason="The published checksums for version @VERSION@ couldn\'t be downloaded (no internet connection, or this version was never released)."\n    fi\n    echo "WARNING: this package is not verified. $reason"\n\n    # \'installer\' on the command line (the app\'s own updater, MDM): nobody to ask\n    if [[ -n $COMMAND_LINE_INSTALL ]]; then\n        echo "Command line install, continuing without asking"\n        return 0\n    fi\n\n    local consoleUser=$(/usr/bin/stat -f%Su /dev/console)\n    if [[ -z $consoleUser || $consoleUser == root || $consoleUser == loginwindow ]]; then\n        echo "No logged-in user to confirm installing an unverified package, cancelling"\n        return 1\n    fi\n\n    local answer\n    answer=$(/bin/launchctl asuser $(/usr/bin/id -u $consoleUser) /usr/bin/sudo -u $consoleUser \\\n        /usr/bin/osascript - "$reason" "${packageHash:-unknown}" 2>/dev/null <<\'APPLESCRIPT\'\non run argv\n    set msg to "This build of BridgePatcher @VERSION@ has not been verified and could include malware." & return & return & (item 1 of argv) & return & return & "Official builds are only published on @SOURCES@. Only continue if you built this package yourself or fully trust where you downloaded it." & return & return & "SHA256: " & (item 2 of argv)\n    try\n        set r to display dialog msg with title "Unverified BridgePatcher build" buttons {"Cancel Installation", "Install Anyway"} default button "Cancel Installation" cancel button "Cancel Installation" with icon caution giving up after 600\n        if gave up of r then return "cancel"\n        return button returned of r\n    on error\n        return "cancel"\n    end try\nend run\nAPPLESCRIPT\n)\n    if [[ $answer == "Install Anyway" ]]; then\n        echo "User chose to install the unverified package"\n        return 0\n    fi\n    echo "Installation of the unverified package cancelled"\n    return 1\n'


class ZSHFunctions:

    def __init__(self) -> None:
        pass


    def generate_standard_pkg_parameters(self) -> str:
        """
        ZSH variables for standard PackageKit parameters
        """

        _script = ""

        _script += "# MARK: PackageKit Parameters\n"
        _script += "# " + "-" * 27 + "\n\n"

        _script += "pathToScript=$0          # ex. /tmp/PKInstallSandbox.*/Scripts/*/preinstall\n"
        _script += "pathToPackage=$1         # ex. ~/Downloads/Installer.pkg\n"
        _script += "pathToTargetLocation=$2  # ex. '/', '/Applications', etc (depends on pkgbuild's '--install-location' argument)\n"
        _script += "pathToTargetVolume=$3    # ex. '/', '/Volumes/MyVolume', etc\n"
        _script += "pathToStartupDisk=$4     # ex. '/'\n"

        return _script


    def generate_script_remove_file(self) -> str:
        """
        ZSH function to remove files
        """

        _script = ""

        _script += "function _removeFile() {\n"
        _script += "    local file=$1\n\n"

        _script += "    if [[ ! -e $file ]]; then\n"
        _script += "        # Check if file is a symbolic link\n"
        _script += "        if [[ -L $file ]]; then\n"
        _script += "            echo \"Removing symbolic link: $file\"\n"
        _script += "            /bin/rm -f $file\n"
        _script += "        fi\n"
        _script += "        return\n"
        _script += "    fi\n\n"

        _script += "    echo \"Removing file: $file\"\n\n"

        _script += "    # Check if file is a directory\n"
        _script += "    if [[ -d $file ]]; then\n"
        _script += "        /bin/rm -rf $file\n"
        _script += "    else\n"
        _script += "        /bin/rm -f $file\n"
        _script += "    fi\n"
        _script += "}\n"

        return _script


    def generate_script_create_parent_directory(self) -> str:
        """
        ZSH function to create parent directory
        """

        _script = ""

        _script += "function _createParentDirectory() {\n"
        _script += "    local file=$1\n\n"

        _script += "    local parentDirectory=\"$(/usr/bin/dirname $file)\"\n\n"

        _script += "    # Check if parent directory exists\n"
        _script += "    if [[ ! -d $parentDirectory ]]; then\n"
        _script += "        echo \"Creating parent directory: $parentDirectory\"\n"
        _script += "        /bin/mkdir -p $parentDirectory\n"
        _script += "    fi\n"
        _script += "}\n"

        return _script


    def generate_set_suid_bit(self) -> str:
        """
        ZSH function to set SUID bit
        """

        _script = ""

        _script += "function _setSUIDBit() {\n"
        _script += "    local binaryPath=$1\n\n"

        _script += "    echo \"Setting SUID bit on: $binaryPath\"\n\n"

        _script += "    # Check if path is a directory\n"
        _script += "    if [[ -d $binaryPath ]]; then\n"
        _script += "        /bin/chmod -R 4755 $binaryPath\n"
        _script += "    else\n"
        _script += "        /bin/chmod 4755 $binaryPath\n"
        _script += "    fi\n"
        _script += "}\n"

        return _script


    def generate_create_alias(self) -> str:
        """
        ZSH function to create alias
        """

        _script = ""

        _script += "function _createAlias() {\n"
        _script += "    local mainPath=$1\n"
        _script += "    local aliasPath=$2\n\n"

        _script += "    # Check if alias path exists\n"
        _script += "    if [[ -e $aliasPath ]]; then\n"
        _script += "        # Check if alias path is a symbolic link\n"
        _script += "        if [[ -L $aliasPath ]]; then\n"
        _script += "            echo \"Removing old symbolic link: $aliasPath\"\n"
        _script += "            /bin/rm -f $aliasPath\n"
        _script += "        else\n"
        _script += "            echo \"Removing old file: $aliasPath\"\n"
        _script += "            /bin/rm -rf $aliasPath\n"
        _script += "        fi\n"
        _script += "    fi\n\n"

        _script += "    # Create symbolic link\n"
        _script += "    echo \"Creating symbolic link: $aliasPath\"\n"
        _script += "    /bin/ln -s $mainPath $aliasPath\n"
        _script += "}\n"

        return _script


    def generate_start_patching(self) -> str:
        """
        ZSH function to start patching
        """

        _script = ""

        _script += "function _startPatching() {\n"
        _script += "    local executable=$1\n"
        _script += "    local logPath=$(_logFile)\n\n"

        _script += "    # Start patching\n"
        _script += "    \"$executable\" \"--patch_sys_vol\" &> $logPath\n"
        _script += "}\n"

        return _script


    def generate_log_file(self) -> str:
        """
        ZSH function to generate log file
        """

        _script = ""

        _script += "function _logFile() {\n"
        _script += "    echo \"/Users/Shared/.OCLP-AutoPatcher-Log-$(/bin/date +\"%Y_%m_%d_%I_%M_%p\").txt\"\n"
        _script += "}\n"

        return _script


    def generate_fix_settings_file_permission(self) -> str:
        """
        ZSH function to fix settings file permission
        """

        _script = ""

        _script += "function _fixSettingsFilePermission() {\n"
        _script += "    local settingsPath=\"$pathToTargetVolume/Users/Shared/.com.dortania.opencore-legacy-patcher.plist\"\n\n"

        _script += "    if [[ -e $settingsPath ]]; then\n"
        _script += "        echo \"Fixing settings file permissions: $settingsPath\"\n"
        _script += "        /bin/chmod 666 $settingsPath\n"
        _script += "    fi\n"

        _script += "}\n"

        return _script


    def generate_reboot(self) -> str:
        """
        ZSH function to reboot
        """

        _script = ""

        _script += "function _reboot() {\n"
        _script += "    /sbin/reboot\n"
        _script += "}\n"

        return _script


    def generate_prewarm_gatekeeper(self) -> str:
        """
        ZSH function to prewarm Gatekeeper
        """

        _script = ""

        _script += "function _prewarmGatekeeper() {\n"
        _script += "    local appPath=$1\n\n"

        _script += "    # Check if /usr/bin/gktool exists\n"
        _script += "    if [[ ! -e /usr/bin/gktool ]]; then\n"
        _script += "        echo \"Host doesn't support Gatekeeper prewarming, skipping...\"\n"
        _script += "        return\n"
        _script += "    fi\n\n"

        _script += "    echo \"Prewarming Gatekeeper for application: $appPath\"\n"
        _script += "    /usr/bin/gktool scan $appPath\n"
        _script += "}\n"

        return _script


    def generate_clean_launch_service(self) -> str:
        """
        ZSH function to clean Launch Service
        """

        _script = ""

        _script += "function _cleanLaunchService() {\n"
        _script += f"    local domain=\"{NEW_DOMAIN}\"\n"
        _script += f"    local legacyDomain=\"{LEGACY_DOMAIN}\"\n"
        _script += f"    local ourApp=\"{constants.APP_BUNDLE_NAME}\"\n"
        _script += f"    local ourLegacyApp=\"{constants.LEGACY_APP_BUNDLE_NAME}\"\n\n"

        _script += "    # Iterate over launch agents and daemons\n"
        _script += "    for launchServiceVariant in \"$pathToTargetVolume/Library/LaunchAgents\" \"$pathToTargetVolume/Library/LaunchDaemons\"; do\n"
        _script += "        # Check if directory exists\n"
        _script += "        if [[ ! -d $launchServiceVariant ]]; then\n"
        _script += "            continue\n"
        _script += "        fi\n\n"

        _script += "        # Our own launch services. Globbed rather than parsed out of\n"
        _script += "        # `ls` output, so filenames cannot be split on whitespace.\n"
        _script += "        for launchServicePath in \"$launchServiceVariant\"/$domain*.plist(N); do\n"
        _script += "            _removeFile \"$launchServicePath\"\n"
        _script += "        done\n\n"

        _script += "        # Installs from before the rename used Dortania's filenames for\n"
        _script += "        # our services. Only remove the ones whose ProgramArguments point\n"
        _script += "        # at our app, so a side-by-side Dortania install keeps its own.\n"
        _script += "        for launchServicePath in \"$launchServiceVariant\"/$legacyDomain*.plist(N); do\n"
        _script += "            local program=$(/usr/libexec/PlistBuddy -c \"Print :ProgramArguments:0\" \"$launchServicePath\" 2>/dev/null)\n"
        _script += "            if [[ $program == *$ourApp* || $program == *$ourLegacyApp* ]]; then\n"
        _script += "                echo \"Removing legacy launch service: $launchServicePath\"\n"
        _script += "                _removeFile \"$launchServicePath\"\n"
        _script += "            fi\n"
        _script += "        done\n"
        _script += "    done\n"
        _script += "}\n"

        return _script


    def generate_migrate_launch_services(self) -> str:
        """
        ZSH function to point existing launch services at the renamed app

        The auto-patch, macos-update and os-caching services are written by the
        app itself (sys_patch/auto_patcher/install.py), not by this package, so
        installs from before the rename still have them pointing at
        OpenCore-Patcher-T2.app/Contents/MacOS/OpenCore-Patcher-T2. That bundle is
        removed by the preinstall script, which would leave launchd with nothing to
        exec until root patches were installed again. Only services whose program
        is exactly the old executable are touched.
        """

        _script = ""

        _script += "function _migrateLaunchServices() {\n"
        _script += f"    local domain=\"{NEW_DOMAIN}\"\n"
        _script += f"    local legacyBinary=\"/{LEGACY_EXECUTABLE_PATH}\"\n"
        _script += f"    local newBinary=\"/{EXECUTABLE_PATH}\"\n\n"

        _script += "    for launchServiceVariant in \"$pathToTargetVolume/Library/LaunchAgents\" \"$pathToTargetVolume/Library/LaunchDaemons\"; do\n"
        _script += "        if [[ ! -d $launchServiceVariant ]]; then\n"
        _script += "            continue\n"
        _script += "        fi\n\n"

        _script += "        for launchServicePath in \"$launchServiceVariant\"/$domain*.plist(N); do\n"
        _script += "            # Never write through a symlink as root\n"
        _script += "            if [[ -L $launchServicePath ]]; then\n"
        _script += "                continue\n"
        _script += "            fi\n"
        _script += "            local program=$(/usr/libexec/PlistBuddy -c \"Print :ProgramArguments:0\" \"$launchServicePath\" 2>/dev/null)\n"
        _script += "            if [[ $program != \"$legacyBinary\" ]]; then\n"
        _script += "                continue\n"
        _script += "            fi\n"
        _script += "            echo \"Pointing launch service at the renamed app: $launchServicePath\"\n"
        _script += "            /usr/bin/plutil -replace ProgramArguments.0 -string \"$newBinary\" \"$launchServicePath\"\n"
        _script += "            /usr/sbin/chown root:wheel \"$launchServicePath\"\n"
        _script += "            /bin/chmod 644 \"$launchServicePath\"\n"
        _script += "        done\n"
        _script += "    done\n"
        _script += "    return 0\n"
        _script += "}\n"

        return _script


    def generate_create_legacy_compatibility_link(self) -> str:
        """
        ZSH function for OpenCore-Patcher-T2.pkg only: link the old bundle name
        to the renamed app inside the install directory

        The built-in updater of builds from before the rename decides whether an
        update succeeded by looking at OpenCore-Patcher-T2.app/Contents/Info.plist.
        Without this link it reports a failed update and opens the macOS Installer
        a second time, although the update was installed. The link is relative, so
        it also resolves when installing to another volume, and it is removed again
        by the next install of either package and by the uninstaller.
        """

        _script = ""

        _script += "function _createLegacyCompatibilityLink() {\n"
        _script += "    local linkPath=\"$pathToTargetVolume/$legacyAppPath\"\n\n"

        _script += "    if [[ -e $linkPath && ! -L $linkPath ]]; then\n"
        _script += "        /bin/rm -rf \"$linkPath\"\n"
        _script += "    fi\n"
        _script += "    echo \"Creating compatibility link: $linkPath\"\n"
        _script += f"    /bin/ln -sfh \"{constants.APP_BUNDLE_NAME}\" \"$linkPath\"\n"
        _script += "}\n"

        return _script


    def generate_clean_legacy_helper(self) -> str:
        """
        ZSH function to clean up the pre-rename Privileged Helper Tool.

        The helper moved to its own path in 8c11f87, but installs from before that
        leave the old one behind as an orphaned setuid-root binary. It cannot simply
        be deleted: Dortania's patcher installs the same file at the same location,
        so remove it only when their app is absent.
        """

        _script = ""

        _script += "function _cleanLegacyHelper() {\n"
        _script += f"    local legacyHelper=\"$pathToTargetVolume/Library/PrivilegedHelperTools/{LEGACY_DOMAIN}.privileged-helper\"\n"
        _script += "    local dortaniaApp=\"$pathToTargetVolume/Library/Application Support/Dortania/OpenCore-Patcher.app\"\n\n"

        _script += "    if [[ ! -e $legacyHelper ]]; then\n"
        _script += "        return\n"
        _script += "    fi\n\n"

        _script += "    if [[ -e $dortaniaApp ]]; then\n"
        _script += "        echo \"Leaving legacy helper in place, Dortania's patcher is installed\"\n"
        _script += "        return\n"
        _script += "    fi\n\n"

        _script += "    echo \"Removing legacy helper: $legacyHelper\"\n"
        _script += "    _removeFile \"$legacyHelper\"\n"
        _script += "}\n"

        return _script


    def generate_clean_legacy_install_location(self) -> str:
        """
        ZSH function to clean up the pre-move install location.

        The app used to install to /Library/Application Support/Dortania, the same
        directory Dortania's patcher uses, and the helper used the
        com.albert-mueller.opencore-legacy-patcher identifier. Both moved; remove
        our copies from the old locations. Every path in legacyFilesToRemove is
        one only this fork ever writes, so nothing of Dortania's is touched, and
        the Dortania directory itself is only removed once it is empty.
        """

        _script = ""

        _script += "function _cleanLegacyInstallLocation() {\n"
        _script += "    for file in $legacyFilesToRemove; do\n"
        _script += "        _removeFile \"$pathToTargetVolume/$file\"\n"
        _script += "    done\n\n"

        _script += "    # rmdir refuses non-empty directories, so a Dortania install stays intact\n"
        _script += "    /bin/rmdir \"$pathToTargetVolume/Library/Application Support/Dortania\" 2>/dev/null\n"
        _script += "    return 0\n"
        _script += "}\n"

        return _script


    def generate_preinstall_main(self) -> str:
        """
        ZSH function for preinstall's main
        """

        _script = ""

        _script += "function _main() {\n"
        _script += "    _cleanLegacyHelper\n"
        _script += "    _cleanLegacyInstallLocation\n"
        _script += "    for file in $filesToRemove; do\n"
        _script += "        _removeFile $pathToTargetVolume/$file\n"
        _script += "        _createParentDirectory $pathToTargetVolume/$file\n"
        _script += "    done\n"
        _script += "}\n"

        return _script


    def generate_verify_package(self) -> str:
        """
        ZSH function that checks the installer's SHA256 against the checksums published on
        the release of this version (SHA256SUMS or certificates.txt) in every update channel's
        repository. If it matches none, the user has to confirm the install in a warning
        dialog; cancelling makes the preinstall script, and so the installation, fail.
        """
        version = constants.Constants().patcher_version
        repos = [channel["repo"].rstrip("/") for channel in constants.Constants().update_channels.values()]
        sources = " or ".join(repo.replace("https://", "") for repo in repos)

        _script = ""
        _script += "function _verifyPackage() {\n"
        _script += "    local -a checksumUrls=(\n"
        for repo in repos:
            for name in ("SHA256SUMS", "certificates.txt"):
                _script += f"        \"{repo}/releases/download/{version}/{name}\"\n"
        _script += "    )\n"
        _script += _VERIFY_BODY.replace("@VERSION@", version).replace("@SOURCES@", sources)
        _script += "}\n"

        return _script


    def generate_load_update_check_agent(self) -> str:
        """
        ZSH function to (re)load the background update check for the logged-in user

        LaunchAgents in /Library/LaunchAgents are only picked up at the next login,
        so without this the first background check would wait until then. Only
        done when installing to the running system with a user logged in.
        """

        _script = ""

        _script += "function _loadUpdateCheckAgent() {\n"
        _script += f"    local label=\"{NEW_DOMAIN}.update-check\"\n"
        _script += "    local agentPath=\"$pathToTargetVolume/Library/LaunchAgents/$label.plist\"\n\n"

        _script += "    if [[ $pathToTargetVolume != \"/\" || ! -f $agentPath ]]; then\n"
        _script += "        return 0\n"
        _script += "    fi\n\n"

        _script += "    /usr/sbin/chown root:wheel \"$agentPath\"\n"
        _script += "    /bin/chmod 644 \"$agentPath\"\n\n"

        _script += "    local consoleUID=$(/usr/bin/stat -f %u /dev/console 2>/dev/null)\n"
        _script += "    if [[ -z $consoleUID || $consoleUID == 0 ]]; then\n"
        _script += "        echo \"No user logged in, update check starts at the next login\"\n"
        _script += "        return 0\n"
        _script += "    fi\n\n"

        _script += "    echo \"Loading update check for UID $consoleUID\"\n"
        _script += "    /bin/launchctl bootout \"gui/$consoleUID/$label\" 2>/dev/null\n"
        _script += "    /bin/launchctl bootstrap \"gui/$consoleUID\" \"$agentPath\" 2>/dev/null || echo \"Failed to load the update check, it starts at the next login\"\n"
        _script += "    return 0\n"
        _script += "}\n"

        return _script


    def generate_unload_update_check_agent(self) -> str:
        """
        ZSH function to stop the background update check before uninstalling,
        so launchd doesn't keep a job for a binary that is about to be removed
        """

        _script = ""

        _script += "function _unloadUpdateCheckAgent() {\n"
        _script += f"    local label=\"{NEW_DOMAIN}.update-check\"\n"
        _script += "    if [[ $pathToTargetVolume != \"/\" ]]; then\n"
        _script += "        return 0\n"
        _script += "    fi\n"
        _script += "    local consoleUID=$(/usr/bin/stat -f %u /dev/console 2>/dev/null)\n"
        _script += "    if [[ -n $consoleUID && $consoleUID != 0 ]]; then\n"
        _script += "        /bin/launchctl bootout \"gui/$consoleUID/$label\" 2>/dev/null\n"
        _script += "    fi\n"
        _script += "    return 0\n"
        _script += "}\n"

        return _script


    def generate_postinstall_main(self, is_autopkg: bool = False, legacy_compatibility: bool = False) -> str:
        """
        ZSH function for postinstall's main
        """

        _script = ""

        _script += "function _main() {\n"
        _script += "    _setSUIDBit \"$pathToTargetVolume/$helperPath\"\n"
        _script += "    _createAlias \"$pathToTargetVolume/$mainAppPath\" \"$pathToTargetVolume/$shimAppPath\"\n"
        if legacy_compatibility:
            _script += "    _createLegacyCompatibilityLink\n"
        _script += "    _migrateLaunchServices\n"
        _script += "    _prewarmGatekeeper \"$pathToTargetVolume/$mainAppPath\"\n"
        _script += "    _loadUpdateCheckAgent\n"
        if is_autopkg:
            _script += "    _startPatching \"$pathToTargetVolume/$executablePath\"\n"
            _script += "    _fixSettingsFilePermission\n"
            _script += "    _reboot\n"
        _script += "}\n"

        return _script


    def generate_uninstall_main(self) -> str:
        """
        ZSH function for uninstall's main
        """

        _script = ""

        _script += "function _main() {\n"
        _script += "    _unloadUpdateCheckAgent\n"
        _script += "    _cleanLaunchService\n"
        _script += "    _cleanLegacyHelper\n"
        _script += "    _cleanLegacyInstallLocation\n"
        _script += "    for file in $filesToRemove; do\n"
        _script += "        _removeFile $pathToTargetVolume/$file\n"
        _script += "    done\n"
        _script += "}\n"

        return _script


class GenerateScripts:

    def __init__(self):
        self.zsh_functions = ZSHFunctions()

        self.files = [
            SHIM_APP_PATH,
            MAIN_APP_PATH,
            "Library/PrivilegedHelperTools/io.github.bridgepatcher.privileged-helper",
            "Library/LaunchAgents/com.albert-mueller.opencore-legacy-patcher.update-check.plist"
        ]

        # Locations used before the app moved out of Dortania's directory and the
        # helper was renamed. Removed on install and uninstall, never recreated.
        self.legacy_files = [
            "Library/Application Support/Dortania/Update.plist",
            "Library/Application Support/Dortania/OpenCore-Patcher-T2.app",
            "Library/PrivilegedHelperTools/com.albert-mueller.opencore-legacy-patcher.privileged-helper",
            # Helper path before the rename to BridgePatcher (only this fork ever wrote it)
            "Library/PrivilegedHelperTools/com.albert-mueller.opencore-patcher-t2.privileged-helper",
            # App bundle and /Applications link before the rename to BridgePatcher.
            # OpenCore-Patcher-T2.pkg puts LEGACY_MAIN_APP_PATH back as a link in
            # its postinstall script, after this cleanup.
            LEGACY_SHIM_APP_PATH,
            LEGACY_MAIN_APP_PATH,
        ]

        self.additional_auto_pkg_files = [
            "Library/LaunchAgents/com.albert-mueller.opencore-legacy-patcher.auto-patch.plist"
        ]


    def __generate_shebang(self) -> str:
        """
        Standard shebang for ZSH
        """
        return "#!/bin/zsh --no-rcs\n"


    def _generate_header_bar(self) -> str:
        """
        # ------------------------------------------------------
        """
        return "# " + "-" * 54 + "\n"


    def _generate_label_bar(self) -> str:
        """
        # ------------------------------
        """
        return "# " + "-" * 27 + "\n"


    def _generate_preinstall_script(self, is_autopkg: bool = False) -> str:
        """
        Generate preinstall script for PKG
        """

        _script = ""

        _script += self.__generate_shebang()

        _script += self._generate_header_bar()
        _script += f"# {'AutoPkg Assets' if is_autopkg else 'OpenCore Legacy Patcher'} Preinstall Script\n"
        _script += self._generate_header_bar()
        _script += "# Remove old files, and prepare directories.\n"
        _script += self._generate_header_bar()
        _script += "\n\n"

        _script += self.zsh_functions.generate_standard_pkg_parameters()
        _script += "\n\n"

        _script += "# MARK: Variables\n"
        _script += self._generate_label_bar()
        _script += "\n"

        # list() copy, not an alias: "_files = self.files" followed by "+=" mutates
        # self.files in place, so generating the AutoPkg preinstall script left the
        # LaunchAgent entry stuck on the instance and every later script - including
        # the uninstaller - acted on a file list it never asked for.
        _files = list(self.files)
        if is_autopkg:
            _files += self.additional_auto_pkg_files

        _script += "filesToRemove=(\n"
        for _file in _files:
            _script += f"    \"{_file}\"\n"

        _script += ")\n"

        _script += "legacyFilesToRemove=(\n"
        for _file in self.legacy_files:
            _script += f"    \"{_file}\"\n"

        _script += ")\n"

        _script += "\n\n"

        _script += "# MARK: Functions\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += self.zsh_functions.generate_script_remove_file()
        _script += "\n"
        _script += self.zsh_functions.generate_script_create_parent_directory()
        _script += "\n"
        _script += self.zsh_functions.generate_clean_legacy_helper()
        _script += "\n"
        _script += self.zsh_functions.generate_clean_legacy_install_location()
        _script += "\n"
        _script += self.zsh_functions.generate_preinstall_main()
        if not is_autopkg:
            _script += "\n"
            _script += self.zsh_functions.generate_verify_package()
        _script += "\n\n"

        _script += "# MARK: Main\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += "echo \"Starting preinstall script...\"\n"
        if not is_autopkg:
            _script += "_verifyPackage || exit 1\n"
        _script += "_main\n"

        return _script


    def _generate_postinstall_script(self, is_autopkg: bool = False, legacy_compatibility: bool = False) -> str:
        """
        """

        _script = ""

        _script += self.__generate_shebang()

        _script += self._generate_header_bar()
        _script += f"# {'AutoPkg Assets' if is_autopkg else 'OpenCore Legacy Patcher'} Post Install Script\n"
        _script += self._generate_header_bar()
        if is_autopkg:
            _script += "# Set UID, create alias, start patching, and reboot.\n"
        else:
            _script += "# Set SUID bit on helper tool, and create app alias.\n"
        _script += self._generate_header_bar()
        _script += "\n\n"

        _script += self.zsh_functions.generate_standard_pkg_parameters()
        _script += "\n\n"

        _script += "# MARK: Variables\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += "helperPath=\"Library/PrivilegedHelperTools/io.github.bridgepatcher.privileged-helper\"\n"
        _script += f"mainAppPath=\"{MAIN_APP_PATH}\"\n"
        _script += f"shimAppPath=\"{SHIM_APP_PATH}\"\n"
        if legacy_compatibility:
            _script += f"legacyAppPath=\"{LEGACY_MAIN_APP_PATH}\"\n"
        if is_autopkg:
            _script += f"executablePath=\"{EXECUTABLE_PATH}\"\n"

        _script += "\n\n"

        _script += "# MARK: Functions\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += self.zsh_functions.generate_set_suid_bit()
        _script += "\n"
        _script += self.zsh_functions.generate_create_alias()
        _script += "\n"
        _script += self.zsh_functions.generate_prewarm_gatekeeper()
        _script += "\n"
        _script += self.zsh_functions.generate_load_update_check_agent()
        _script += "\n"
        _script += self.zsh_functions.generate_migrate_launch_services()
        _script += "\n"
        if legacy_compatibility:
            _script += self.zsh_functions.generate_create_legacy_compatibility_link()
            _script += "\n"
        if is_autopkg:
            _script += self.zsh_functions.generate_start_patching()
            _script += "\n"
            _script += self.zsh_functions.generate_log_file()
            _script += "\n"
            _script += self.zsh_functions.generate_fix_settings_file_permission()
            _script += "\n"
            _script += self.zsh_functions.generate_reboot()
            _script += "\n"

        _script += self.zsh_functions.generate_postinstall_main(is_autopkg, legacy_compatibility)
        _script += "\n\n"

        _script += "# MARK: Main\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += "echo \"Starting postinstall script...\"\n"
        _script += "_main\n"

        return _script


    def _generate_uninstall_script(self) -> str:
        """
        """
        _script = ""

        _script += self.__generate_shebang()

        _script += self._generate_header_bar()
        _script += "# OpenCore Legacy Patcher Uninstall Script\n"
        _script += self._generate_header_bar()
        _script += "# Remove OpenCore Legacy Patcher files and directories.\n"
        _script += self._generate_header_bar()
        _script += "\n\n"

        _script += self.zsh_functions.generate_standard_pkg_parameters()
        _script += "\n\n"

        _script += "# MARK: Variables\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _files = self.files

        _script += "filesToRemove=(\n"
        for _file in _files:
            _script += f"    \"{_file}\"\n"

        _script += ")\n"

        _script += "legacyFilesToRemove=(\n"
        for _file in self.legacy_files:
            _script += f"    \"{_file}\"\n"

        _script += ")\n"

        _script += "\n\n"

        _script += "# MARK: Functions\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += self.zsh_functions.generate_script_remove_file()
        _script += "\n"
        _script += self.zsh_functions.generate_clean_launch_service()
        _script += "\n"
        _script += self.zsh_functions.generate_unload_update_check_agent()
        _script += "\n"
        _script += self.zsh_functions.generate_clean_legacy_helper()
        _script += "\n"
        _script += self.zsh_functions.generate_clean_legacy_install_location()
        _script += "\n"
        _script += self.zsh_functions.generate_uninstall_main()
        _script += "\n\n"

        _script += "# MARK: Main\n"
        _script += self._generate_label_bar()
        _script += "\n"

        _script += "echo \"Starting uninstall script...\"\n"
        _script += "_main\n"

        return _script


    def preinstall_pkg(self) -> str:
        """
        Generate preinstall script for PKG
        """
        return self._generate_preinstall_script()


    def preinstall_autopkg(self) -> str:
        """
        Generate preinstall script for AutoPkg
        """
        return self._generate_preinstall_script(is_autopkg=True)


    def postinstall_pkg(self, legacy_compatibility: bool = False) -> str:
        """
        Generate postinstall script for PKG

        legacy_compatibility: for OpenCore-Patcher-T2.pkg, see
        generate_create_legacy_compatibility_link()
        """
        return self._generate_postinstall_script(legacy_compatibility=legacy_compatibility)


    def postinstall_autopkg(self) -> str:
        """
        Generate postinstall script for AutoPkg
        """
        return self._generate_postinstall_script(is_autopkg=True)


    def uninstall(self) -> str:
        """
        Generate uninstall script
        """
        return self._generate_uninstall_script()
