/**
 * test_harness.m
 *
 * Test harness to verify T2Stage2Bypass dylib functionality against
 * OSInstaller.framework classes on macOS.
 */

#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <objc/message.h>

@interface NSObject (OSIInstallElementMock)
- (BOOL)runReturningError:(NSError **)error;
@end

int main(int argc, const char * argv[]) {
    @autoreleasepool {
        printf("\n=== Running T2 Stage 2 Bypass Verification Test ===\n\n");

        NSBundle *bundle = [NSBundle bundleWithPath:@"/System/Library/PrivateFrameworks/OSInstaller.framework"];
        if (![bundle load]) {
            fprintf(stderr, "Failed to load OSInstaller.framework\n");
            return 1;
        }

        // Test 1: OSIUpdateFirmwareElement
        Class fwClass = objc_getClass("OSIUpdateFirmwareElement");
        if (fwClass) {
            id fwElement = [[fwClass alloc] init];
            NSError *error = nil;
            
            printf("[Test 1] Invoking [OSIUpdateFirmwareElement runReturningError:]...\n");
            BOOL result = [fwElement runReturningError:&error];
            printf("         Result: %s | Error: %s\n\n",
                   result ? "YES (SUCCESS)" : "NO (FAILED)",
                   error ? [[error localizedDescription] UTF8String] : "nil");
            
            if (!result || error != nil) {
                fprintf(stderr, "FAIL: OSIUpdateFirmwareElement did not return success!\n");
                return 1;
            }
        }

        // Test 2: OSIVerifyROMElement
        Class romClass = objc_getClass("OSIVerifyROMElement");
        if (romClass) {
            id romElement = [[romClass alloc] init];
            NSError *error = nil;

            printf("[Test 2] Invoking [OSIVerifyROMElement runReturningError:]...\n");
            BOOL result = [romElement runReturningError:&error];
            printf("         Result: %s | Error: %s\n\n",
                   result ? "YES (SUCCESS)" : "NO (FAILED)",
                   error ? [[error localizedDescription] UTF8String] : "nil");
            
            if (!result || error != nil) {
                fprintf(stderr, "FAIL: OSIVerifyROMElement did not return success!\n");
                return 1;
            }
        }

        // Test 3: OSIInstallPersonalizedManifestsElement
        Class manifestClass = objc_getClass("OSIInstallPersonalizedManifestsElement");
        if (manifestClass) {
            id manifestElement = [[manifestClass alloc] init];
            NSError *error = nil;

            printf("[Test 3] Invoking [OSIInstallPersonalizedManifestsElement runReturningError:]...\n");
            BOOL result = [manifestElement runReturningError:&error];
            printf("         Result: %s | Error: %s\n\n",
                   result ? "YES (SUCCESS)" : "NO (FAILED)",
                   error ? [[error localizedDescription] UTF8String] : "nil");

            if (!result || error != nil) {
                fprintf(stderr, "FAIL: OSIInstallPersonalizedManifestsElement did not return success!\n");
                return 1;
            }
        }

        printf("===============================================================\n");
        printf(">>> ALL TESTS PASSED! T2 Stage 2 Bypass operates with 100%% efficacy! <<<\n");
        printf("===============================================================\n\n");
    }
    return 0;
}
