#import <Foundation/Foundation.h>
#import <objc/runtime.h>

// Dummy implementation of Metal Device for Photos and Widgets on non-Metal GPUs

@interface MockMTLDevice : NSObject
@end

@implementation MockMTLDevice

- (NSString *)name {
    return @"Mock Metal Device (Non-Metal Fallback)";
}

// Intercept methods that expect a real Metal device and return nil or dummy objects
// to prevent the app from aborting.

- (NSMethodSignature *)methodSignatureForSelector:(SEL)sel {
    NSMethodSignature *sig = [super methodSignatureForSelector:sel];
    if (!sig) {
        // Return a dummy signature returning an object (id) for any unhandled method
        sig = [NSMethodSignature signatureWithObjCTypes:"@@:"];
    }
    return sig;
}

- (void)forwardInvocation:(NSInvocation *)invocation {
    NSLog(@"[Tahoe-NonMetal] Swallowed call to MockMTLDevice: %@", NSStringFromSelector(invocation.selector));
    // Return nil for object return types to prevent crashes
    void *nullPtr = NULL;
    [invocation setReturnValue:&nullPtr];
}

@end

id MTLCreateSystemDefaultDevice(void) {
    NSLog(@"[Tahoe-NonMetal] Intercepted MTLCreateSystemDefaultDevice, returning MockMTLDevice");
    return [[MockMTLDevice alloc] init];
}

__attribute__((constructor))
static void MetalMockInit(void) {
    NSLog(@"[Tahoe-NonMetal] MetalMock initialized - Ready to stub Metal frameworks");
}
