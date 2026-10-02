/*
    ------------------------------------------------
    OpenCore Legacy Patcher Privileged Helper Tool
    ------------------------------------------------
    Designed as an alternative to an XPC service,
    this tool is used to run commands as root.
    ------------------------------------------------
    Server and client must have the same signing
    certificate in order to run commands.
    ------------------------------------------------
*/

#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <libproc.h>
#include <limits.h>
#include <stdlib.h>
#include <sys/stat.h>

#define UTILITY_VERSION "1.0.0"

#define OCLP_PHT_ERROR_MISSING_ARGUMENTS           160
#define OCLP_PHT_ERROR_SET_UID_MISSING             161
#define OCLP_PHT_ERROR_SET_UID_FAILED              162
#define OCLP_PHT_ERROR_SELF_PATH_MISSING           163
#define OCLP_PHT_ERROR_PARENT_PATH_MISSING         164
#define OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING 165
#define OCLP_PHT_ERROR_INVALID_TEAM_ID             166
#define OCLP_PHT_ERROR_INVALID_CERTIFICATES        167
#define OCLP_PHT_ERROR_COMMAND_MISSING             168
#define OCLP_PHT_ERROR_COMMAND_FAILED              169
#define OCLP_PHT_ERROR_CATCH_ALL                   170
#define OCLP_PHT_ERROR_COMMAND_NOT_ALLOWED         171


NSDictionary *getSigningInformationFromPath(NSString *path) {
    SecStaticCodeRef codeRef;
    OSStatus status = SecStaticCodeCreateWithPath((__bridge CFURLRef)[NSURL fileURLWithPath:path], kSecCSDefaultFlags, &codeRef);
    if (status != errSecSuccess) {
        return nil;
    }

    CFDictionaryRef codeDict = NULL;
    status = SecCodeCopySigningInformation(codeRef, kSecCSSigningInformation, &codeDict);
    if (status != errSecSuccess) {
        return nil;
    }

    return (__bridge NSDictionary *)codeDict;
}

NSString *getParentProcessPath() {
    char pathbuf[PROC_PIDPATHINFO_MAXSIZE];
    if (proc_pidpath(getppid(), pathbuf, sizeof(pathbuf)) <= 0) {
        return nil;
    }
    NSString *path = [NSString stringWithUTF8String:pathbuf];
    return path;
}

NSString *getProcessPath() {
    NSString *path = [[NSBundle mainBundle] executablePath];
    return path;
}

BOOL isSBitSet(NSString *path) {
    NSFileManager *fileManager = [NSFileManager defaultManager];
    NSDictionary *attributes = [fileManager attributesOfItemAtPath:path error:nil];
    if (attributes == nil) {
        return NO;
    }
    return (attributes.filePosixPermissions & S_ISUID) != 0;
}

#ifdef DEBUG
/*
    Command allowlist (DEBUG builds ONLY)
    ------------------------------------------------
    A DEBUG build skips the certificate check, so any local process can talk
    to this helper. To limit the damage, every command in a DEBUG build is
    resolved with realpath() and must match one of the binaries the app
    actually needs. All entries are SIP-protected system paths, so they cannot
    be swapped out by an unprivileged attacker.

    Release builds do NOT use this list: there the caller is verified by its
    signing certificates (see main()), and any command it passes is executed.
    Everything in this block is compiled out of release builds entirely.

    Keep this list in sync with the run_as_root() call sites in the Python app.
    A rejected command returns OCLP_PHT_ERROR_COMMAND_NOT_ALLOWED. The app treats
    that as final and does NOT retry the command through an administrator-password
    prompt - otherwise every command refused here would still run as root, just
    one password dialog later (see _HELPER_REFUSAL_ERRORS in subprocess_wrapper.py).
*/
static NSSet<NSString *> *allowedCommands(void) {
    static NSSet *set = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        set = [NSSet setWithArray:@[
            @"/bin/chmod",
            @"/bin/cp",
            @"/bin/launchctl",
            @"/bin/mkdir",
            @"/bin/mv",
            @"/bin/rm",
            @"/bin/sh",
            @"/sbin/mount",
            @"/sbin/umount",
            @"/usr/bin/chflags",
            @"/usr/bin/codesign",
            @"/usr/bin/defaults",
            @"/usr/bin/hdiutil",
            @"/usr/bin/killall",
            @"/usr/bin/kmutil",
            @"/usr/bin/rsync",
            @"/usr/bin/tar",
            @"/usr/bin/touch",
            @"/usr/bin/xar",
            @"/usr/sbin/bless",
            @"/usr/sbin/chown",
            @"/usr/sbin/diskutil",
            @"/usr/sbin/installer",
            @"/usr/sbin/kcditto",
            @"/usr/sbin/kextcache",
        ]];
    });
    return set;
}

/*
    Commands that are a direct "run arbitrary code as root" primitive
    (script interpreter, package installer with its own install scripts).
    Refused outright in DEBUG builds, because there the caller is not verified.
*/
static NSSet<NSString *> *debugForbiddenCommands(void) {
    static NSSet *set = nil;
    static dispatch_once_t once;
    dispatch_once(&once, ^{
        set = [NSSet setWithArray:@[
            @"/bin/sh",
            @"/usr/sbin/installer",
        ]];
    });
    return set;
}

#endif /* DEBUG */

NSString *resolveCommandPath(const char *rawPath) {
    // Only absolute paths - never rely on PATH lookup.
    if (rawPath == NULL || rawPath[0] != '/') {
        return nil;
    }
    char resolved[PATH_MAX];
    if (realpath(rawPath, resolved) == NULL) {
        return nil;
    }
    struct stat st;
    if (stat(resolved, &st) != 0 || !S_ISREG(st.st_mode)) {
        return nil;
    }
    return [NSString stringWithUTF8String:resolved];
}

#ifdef DEBUG

BOOL isCommandAllowed(NSString *command, NSArray<NSString *> *arguments, NSDictionary *helperSigningInformation) {
    if ([allowedCommands() containsObject:command]) {
        if ([debugForbiddenCommands() containsObject:command]) {
            return NO;
        }

        // /bin/sh is only used to run the generated Installer.sh:
        // exactly one argument, and no options such as -c.
        if ([command isEqualToString:@"/bin/sh"]) {
            if (arguments.count != 1 || [arguments[0] hasPrefix:@"-"]) {
                return NO;
            }
        }
        return YES;
    }

    // RSRRepair ships inside the app bundle, so its path is not fixed.
    // Accept it only if it carries the same signing certificates as this helper.
    // (Unsigned DEBUG builds have no certificates, so this is always refused there.)
    if ([[command lastPathComponent] isEqualToString:@"RSRRepair"]) {
        NSDictionary *commandSigningInformation = getSigningInformationFromPath(command);
        NSArray *helperCertificates  = helperSigningInformation[@"certificates"];
        NSArray *commandCertificates = commandSigningInformation[@"certificates"];
        if (helperCertificates.count > 0 &&
            commandCertificates.count > 0 &&
            [helperCertificates isEqualToArray:commandCertificates]) {
            return YES;
        }
    }

    return NO;
}
#endif /* DEBUG */


int main(int argc, const char * argv[]) {
    @autoreleasepool {
        // We simply return if no arguments are passed
        if (argc < 2) {
            return OCLP_PHT_ERROR_MISSING_ARGUMENTS;
        }

        if (argc == 2 && (strcmp(argv[1], "--version") == 0 || strcmp(argv[1], "-v") == 0)) {
            printf("%s\n", UTILITY_VERSION);
            return 0;
        }

        // Verify whether we can run as root
        NSString *processPath = getProcessPath();
        if (processPath == nil) {
            return OCLP_PHT_ERROR_SELF_PATH_MISSING;
        }

        if (!isSBitSet(processPath)) {
            return OCLP_PHT_ERROR_SET_UID_MISSING;
        }

        setuid(0);
        if (getuid() != 0) {
            return OCLP_PHT_ERROR_SET_UID_FAILED;
        }

        NSString *parentProcessPath = getParentProcessPath();
        if (parentProcessPath == nil) {
            return OCLP_PHT_ERROR_PARENT_PATH_MISSING;
        }

        NSDictionary *processSigningInformation = getSigningInformationFromPath(processPath);
        NSDictionary *parentProcessSigningInformation = getSigningInformationFromPath(parentProcessPath);

        if (processSigningInformation == nil || parentProcessSigningInformation == nil) {
            return OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING;
        }

        #ifdef DEBUG
        // Certificate check is skipped in debug mode, so any local process can
        // talk to this helper. The DEBUG-only command allowlist below is what
        // limits the damage.
        // DO NOT USE IN PRODUCTION - prefer a self-signed release build.
        #else
        // Check Certificates
        if (processSigningInformation[@"certificates"] == nil ||
            parentProcessSigningInformation[@"certificates"] == nil ||
            ![processSigningInformation[@"certificates"]
                isEqualToArray:parentProcessSigningInformation[@"certificates"]]) {
            return OCLP_PHT_ERROR_INVALID_CERTIFICATES;
        }
        #endif

        NSString *command = resolveCommandPath(argv[1]);
        if (command == nil) {
            return OCLP_PHT_ERROR_COMMAND_MISSING;
        }

        NSMutableArray<NSString *> *arguments = [NSMutableArray array];
        for (int i = 2; i < argc; i++) {
            NSString *argument = [NSString stringWithUTF8String:argv[i]];
            if (argument == nil) {
                // Not valid UTF-8 - refuse instead of silently dropping/crashing
                return OCLP_PHT_ERROR_COMMAND_NOT_ALLOWED;
            }
            [arguments addObject:argument];
        }

        #ifdef DEBUG
        // Only DEBUG builds restrict the command set - release builds already
        // verified the caller's signing certificates above.
        if (!isCommandAllowed(command, arguments, processSigningInformation)) {
            return OCLP_PHT_ERROR_COMMAND_NOT_ALLOWED;
        }
        #endif

        NSTask *task = [[NSTask alloc] init];
        [task setLaunchPath:command];
        [task setArguments:arguments];
        // Do not inherit the caller's environment into a root process
        // (DYLD_*, PATH, BASH_ENV, TMPDIR, ... are all attacker-controlled).
        [task setEnvironment:@{
            @"PATH": @"/usr/bin:/bin:/usr/sbin:/sbin",
        }];
        [task launch];
        [task waitUntilExit];
        return [task terminationStatus];
    }
    return OCLP_PHT_ERROR_CATCH_ALL; // Should never reach here
}