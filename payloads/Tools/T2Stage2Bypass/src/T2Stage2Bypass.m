/**
 * T2Stage2Bypass.m
 * 
 * OpenCore Legacy Patcher T2 - macOS Tahoe Installer Stage 2 Bypass
 * 
 * Intercepts OSInstaller.framework queue execution during Stage 2
 * to prevent unsupported T2 Macs (2018-2019) from failing BridgeOS/ROM
 * firmware personalization and verification checks.
 *
 * Co-Authors: Matteo (@Medelcartelinc) & Contributors
 */

#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <os/log.h>

#define BYPASS_LOG(fmt, ...) \
    os_log(OS_LOG_DEFAULT, "[T2Stage2Bypass] " fmt, ##__VA_ARGS__); \
    fprintf(stderr, "[T2Stage2Bypass] " fmt "\n", ##__VA_ARGS__)

// Replacement implementation for `- (BOOL)runReturningError:(NSError **)`
static BOOL Swizzled_runReturningError_Success(id self, SEL _cmd, NSError **error) {
    const char *clsName = class_getName([self class]);
    const char *selName = sel_getName(_cmd);
    
    BYPASS_LOG(">>> Intercepted -[%s %s] -> FORCING SUCCESS (YES)", clsName, selName);
    
    if (error != NULL) {
        *error = nil;
    }
    return YES;
}

// Replacement returning YES
static BOOL Swizzled_returnYES(id self, SEL _cmd) {
    const char *clsName = class_getName([self class]);
    const char *selName = sel_getName(_cmd);
    BYPASS_LOG(">>> Intercepted -[%s %s] -> FORCING YES", clsName, selName);
    return YES;
}

// Replacement returning NO
static BOOL Swizzled_returnNO(id self, SEL _cmd) {
    const char *clsName = class_getName([self class]);
    const char *selName = sel_getName(_cmd);
    BYPASS_LOG(">>> Intercepted -[%s %s] -> FORCING NO", clsName, selName);
    return NO;
}

static void HookInstanceMethod(Class cls, SEL selector, IMP newImp, const char *types) {
    if (!cls) return;
    
    Method origMethod = class_getInstanceMethod(cls, selector);
    if (origMethod) {
        method_setImplementation(origMethod, newImp);
        BYPASS_LOG("Hooked method: -[%s %s]", class_getName(cls), sel_getName(selector));
    } else {
        class_addMethod(cls, selector, newImp, types);
        BYPASS_LOG("Added method: -[%s %s]", class_getName(cls), sel_getName(selector));
    }
}

__attribute__((constructor))
static void T2Stage2Bypass_Initialize(void) {
    BYPASS_LOG("===============================================================");
    BYPASS_LOG("T2 Stage 2 Firmware Bypass Loaded into process: %d (%s)",
               getpid(), getprogname() ? getprogname() : "unknown");
    BYPASS_LOG("===============================================================");

    // Ensure OSInstaller.framework is loaded into memory
    NSBundle *osInstallerBundle = [NSBundle bundleWithPath:@"/System/Library/PrivateFrameworks/OSInstaller.framework"];
    if (osInstallerBundle && ![osInstallerBundle isLoaded]) {
        [osInstallerBundle load];
    }

    // 1. Hook OSIUpdateFirmwareElement (The primary Stage 2 failure point)
    Class fwClass = objc_getClass("OSIUpdateFirmwareElement");
    if (fwClass) {
        BYPASS_LOG("Targeting OSIUpdateFirmwareElement...");
        HookInstanceMethod(fwClass, @selector(runReturningError:), (IMP)Swizzled_runReturningError_Success, "c@:^@");
        HookInstanceMethod(fwClass, sel_registerName("firmwareUpdatesQueued"), (IMP)Swizzled_returnNO, "c@:");
    } else {
        BYPASS_LOG("WARNING: OSIUpdateFirmwareElement class not found in current process.");
    }

    // 2. Hook OSIVerifyROMElement (ROM and firmware capability checks)
    Class romClass = objc_getClass("OSIVerifyROMElement");
    if (romClass) {
        BYPASS_LOG("Targeting OSIVerifyROMElement...");
        HookInstanceMethod(romClass, @selector(runReturningError:), (IMP)Swizzled_runReturningError_Success, "c@:^@");
        HookInstanceMethod(romClass, sel_registerName("checkROMFeatures"), (IMP)Swizzled_returnYES, "c@:");
        HookInstanceMethod(romClass, sel_registerName("ignoreIncompatibleROMFeatures"), (IMP)Swizzled_returnYES, "c@:");
        HookInstanceMethod(romClass, sel_registerName("apfsSupportedByROM"), (IMP)Swizzled_returnYES, "c@:");
        HookInstanceMethod(romClass, sel_registerName("largeBaseSystemsSupportedByROM"), (IMP)Swizzled_returnYES, "c@:");
    }

    // 3. Hook OSIInstallPersonalizedManifestsElement (Apple im4m personalization ticket requests)
    Class manifestClass = objc_getClass("OSIInstallPersonalizedManifestsElement");
    if (manifestClass) {
        BYPASS_LOG("Targeting OSIInstallPersonalizedManifestsElement...");
        HookInstanceMethod(manifestClass, @selector(runReturningError:), (IMP)Swizzled_runReturningError_Success, "c@:^@");
    }

    BYPASS_LOG("All Stage 2 firmware & ROM bypass hooks installed successfully.");
}
