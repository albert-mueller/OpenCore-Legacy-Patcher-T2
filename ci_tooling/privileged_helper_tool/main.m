/*
    ------------------------------------------------
    OpenCore Legacy Patcher Privileged Helper Tool
    ------------------------------------------------
    Designed as an alternative to an XPC service,
    this tool is used to run commands as root.
    ------------------------------------------------
    Release builds only accept a caller with the
    app's identifier, signed with the exact leaf
    certificate this helper is signed with, using
    the hardened runtime (see "Caller pinning").
    ------------------------------------------------
*/

#import <Foundation/Foundation.h>
#import <Security/Security.h>
#include <CommonCrypto/CommonDigest.h>
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
#define OCLP_PHT_ERROR_CALLER_NOT_HARDENED         172

/*
    Caller pinning (release builds)
    ------------------------------------------------
    A caller is accepted only if it satisfies the code requirement

        identifier "<OCLP_CLIENT_IDENTIFIER>" and certificate leaf = H"<SHA-1>"

    and its signature validates (dynamically for the running process, strictly
    for the code on disk), and it uses the hardened runtime.

    <SHA-1> is the leaf certificate of THIS helper's own, validated signature, so
    the helper only trusts apps signed with the exact certificate it was signed
    with - nothing that merely has a matching bundle ID or a look-alike chain.
    Build with -DOCLP_PINNED_LEAF_SHA1=\"<40 hex chars>\" (make CERT_SHA1=...)
    to additionally hard-code the expected hash; the helper then also refuses to
    run if its own signature does not carry that certificate.

    Before this change the helper only compared the certificate arrays returned
    by SecCodeCopySigningInformation() without ever validating either signature.
    Those certificates are public, and an unvalidated signature blob can be
    grafted onto any binary, so a modified copy of the app passed the check.
*/
#ifndef OCLP_CLIENT_IDENTIFIER
#define OCLP_CLIENT_IDENTIFIER "com.dortania.opencore-legacy-patcher-t2"
#endif

// kSecCodeSignatureRuntime (SDK 10.14+); the literal keeps older SDKs building.
#define OCLP_CS_RUNTIME_FLAG 0x10000


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

#ifndef DEBUG
static NSString *leafCertificateSHA1(NSDictionary *signingInformation) {
    NSArray *certificates = signingInformation[(__bridge NSString *)kSecCodeInfoCertificates];
    if (certificates.count == 0) {
        return nil;
    }
    SecCertificateRef leaf = (__bridge SecCertificateRef)certificates[0];
    CFDataRef der = SecCertificateCopyData(leaf);
    if (der == NULL) {
        return nil;
    }
    unsigned char digest[CC_SHA1_DIGEST_LENGTH];
    CC_SHA1(CFDataGetBytePtr(der), (CC_LONG)CFDataGetLength(der), digest);
    CFRelease(der);

    NSMutableString *hex = [NSMutableString stringWithCapacity:CC_SHA1_DIGEST_LENGTH * 2];
    for (int i = 0; i < CC_SHA1_DIGEST_LENGTH; i++) {
        [hex appendFormat:@"%02X", digest[i]];
    }
    return hex;
}

/*
    Validate this helper's own signature and return the SHA-1 of its leaf
    certificate - the pin every caller is checked against. nil = refuse.
*/
static NSString *validatedSelfLeafSHA1(NSString *processPath) {
    SecStaticCodeRef selfCode = NULL;
    if (SecStaticCodeCreateWithPath((__bridge CFURLRef)[NSURL fileURLWithPath:processPath],
                                    kSecCSDefaultFlags, &selfCode) != errSecSuccess) {
        return nil;
    }

    NSString *sha1 = nil;
    CFDictionaryRef info = NULL;
    if (SecStaticCodeCheckValidity(selfCode, kSecCSStrictValidate | kSecCSCheckAllArchitectures, NULL) == errSecSuccess &&
        SecCodeCopySigningInformation(selfCode, kSecCSSigningInformation, &info) == errSecSuccess) {
        sha1 = leafCertificateSHA1((__bridge NSDictionary *)info);
    }
    if (info != NULL) CFRelease(info);
    CFRelease(selfCode);

#ifdef OCLP_PINNED_LEAF_SHA1
    if (sha1 == nil || [sha1 caseInsensitiveCompare:@OCLP_PINNED_LEAF_SHA1] != NSOrderedSame) {
        return nil;
    }
#endif
    return sha1;
}

/*
    Validate the process that launched us. Uses the running process (by pid),
    not just a path on disk, so a binary swapped after launch or a process whose
    code pages were invalidated is rejected as well.
*/
static int validateCaller(NSString *pinnedSHA1) {
    pid_t parentPid = getppid();
    if (parentPid <= 1) {
        return OCLP_PHT_ERROR_PARENT_PATH_MISSING;
    }

    NSString *requirementString = [NSString stringWithFormat:
        @"identifier \"%s\" and certificate leaf = H\"%@\"", OCLP_CLIENT_IDENTIFIER, pinnedSHA1];

    SecRequirementRef requirement = NULL;
    SecCodeRef        guest       = NULL;
    SecStaticCodeRef  guestStatic = NULL;
    CFDictionaryRef   guestInfo   = NULL;
    NSDictionary     *attributes  = nil;
    NSNumber         *flags       = nil;
    int result = OCLP_PHT_ERROR_INVALID_CERTIFICATES;

    if (SecRequirementCreateWithString((__bridge CFStringRef)requirementString,
                                       kSecCSDefaultFlags, &requirement) != errSecSuccess) {
        goto out;
    }

    attributes = @{ (__bridge NSString *)kSecGuestAttributePid: @(parentPid) };
    if (SecCodeCopyGuestWithAttributes(NULL, (__bridge CFDictionaryRef)attributes,
                                       kSecCSDefaultFlags, &guest) != errSecSuccess) {
        goto out;
    }

    // Running process: dynamic validity + requirement (also checks the bundle's resources)
    if (SecCodeCheckValidity(guest, kSecCSDefaultFlags, requirement) != errSecSuccess) {
        goto out;
    }

    // Same code on disk: strict validation + requirement
    if (SecCodeCopyStaticCode(guest, kSecCSDefaultFlags, &guestStatic) != errSecSuccess ||
        SecStaticCodeCheckValidity(guestStatic, kSecCSStrictValidate | kSecCSCheckAllArchitectures, requirement) != errSecSuccess) {
        goto out;
    }

    // Hardened runtime: without it, DYLD_INSERT_LIBRARIES or a debugger attached
    // by the same user could make the genuine, validly signed app call us.
    if (SecCodeCopySigningInformation(guestStatic, kSecCSSigningInformation, &guestInfo) != errSecSuccess) {
        goto out;
    }
    flags = ((__bridge NSDictionary *)guestInfo)[(__bridge NSString *)kSecCodeInfoFlags];
    if (flags == nil || (flags.unsignedIntValue & OCLP_CS_RUNTIME_FLAG) == 0) {
        result = OCLP_PHT_ERROR_CALLER_NOT_HARDENED;
        goto out;
    }

    // We were reparented (caller exited) while validating - the pid is no longer our caller.
    if (getppid() != parentPid) {
        result = OCLP_PHT_ERROR_PARENT_PATH_MISSING;
        goto out;
    }

    result = 0;

out:
    if (guestInfo   != NULL) CFRelease(guestInfo);
    if (guestStatic != NULL) CFRelease(guestStatic);
    if (guest       != NULL) CFRelease(guest);
    if (requirement != NULL) CFRelease(requirement);
    return result;
}
#endif /* !DEBUG */

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

        #ifdef DEBUG
        // Certificate check is skipped in debug mode, so any local process can
        // talk to this helper. The DEBUG-only command allowlist below is what
        // limits the damage.
        // DO NOT USE IN PRODUCTION - prefer a self-signed release build.
        NSString *parentProcessPath = getParentProcessPath();
        if (parentProcessPath == nil) {
            return OCLP_PHT_ERROR_PARENT_PATH_MISSING;
        }
        NSDictionary *processSigningInformation = getSigningInformationFromPath(processPath);
        if (processSigningInformation == nil || getSigningInformationFromPath(parentProcessPath) == nil) {
            return OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING;
        }
        #else
        // Pin to our own (validated) leaf certificate, then require the caller
        // to match it - see "Caller pinning" at the top of this file.
        NSString *pinnedSHA1 = validatedSelfLeafSHA1(processPath);
        if (pinnedSHA1 == nil) {
            return OCLP_PHT_ERROR_SIGNING_INFORMATION_MISSING;
        }
        int callerStatus = validateCaller(pinnedSHA1);
        if (callerStatus != 0) {
            return callerStatus;
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