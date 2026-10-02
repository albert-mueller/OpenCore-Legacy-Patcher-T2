/*
 * metal_31001_interposer.m
 *
 * Metal 31001 Pipeline Descriptor Interposer for Intel Skylake GPUs
 * Targets: macOS Tahoe 26.x (Darwin 25) & Monterey 12.5 Metal Driver
 *
 * Interposes MTLRenderPipelineDescriptorInternal and MTLComputePipelineDescriptorInternal
 * to translate modern macOS Tahoe descriptor layouts into the legacy layout expected by
 * AppleIntelSKLGraphicsMTLDriver.
 */

#import <Foundation/Foundation.h>
#import <objc/runtime.h>
#import <pthread.h>
#import <mach-o/dyld.h>
#import <dlfcn.h>

static void *(*real_render)(id, SEL) = NULL;
static void *(*real_compute)(id, SEL) = NULL;

static pthread_key_t threadMontereyRenderKey;
static pthread_once_t threadMontereyRenderOnce = PTHREAD_ONCE_INIT;

static pthread_key_t threadMontereyComputeKey;
static pthread_once_t threadMontereyComputeOnce = PTHREAD_ONCE_INIT;

static void initRenderKey(void) {
    pthread_key_create(&threadMontereyRenderKey, free);
}

static void initComputeKey(void) {
    pthread_key_create(&threadMontereyComputeKey, free);
}

static void *getOrMakeThreadStorage(pthread_once_t *onceControl, pthread_key_t *key, size_t size, void (*initFunc)(void)) {
    pthread_once(onceControl, initFunc);
    void *ptr = pthread_getspecific(*key);
    if (!ptr) {
        ptr = calloc(1, size);
        pthread_setspecific(*key, ptr);
    }
    return ptr;
}

static BOOL inDSC(void *addr) {
    // If the caller is within system dyld shared cache
    Dl_info info;
    if (dladdr(addr, &info) && info.dli_fname) {
        // Legacy drivers are loaded from Extensions, do NOT skip the shim for them
        if (strstr(info.dli_fname, "Extensions") || strstr(info.dli_fname, "AppleIntel") || strstr(info.dli_fname, "AMD")) {
            return NO;
        }
        // Skip shim for native Tahoe system frameworks
        if (strstr(info.dli_fname, "dyld_shared_cache") || strstr(info.dli_fname, "/System/Library/Frameworks/") || strstr(info.dli_fname, "/System/Library/PrivateFrameworks/")) {
            return YES;
        }
    }
    return NO;
}

static void *fake_render(id self, SEL _cmd) {
    void *modern = real_render ? real_render(self, _cmd) : NULL;
    if (!modern) return NULL;

    void *caller = __builtin_return_address(0);
    if (inDSC(caller)) {
        return modern;
    }

    // Monterey expects 0x190 bytes for the legacy descriptor
    char *legacy = (char *)getOrMakeThreadStorage(&threadMontereyRenderOnce, &threadMontereyRenderKey, 0x190, initRenderKey);
    char *src = (char *)modern;

    // Header & base fields
    *(uint64_t *)(legacy + 0x00) = *(uint64_t *)(src + 0x00);
    memcpy(legacy + 0x08, src + 0x08, 0x40);

    // Color attachments & pixel formats
    *(uint64_t *)(legacy + 0x48) = *(uint64_t *)(src + 0x48);
    *(uint64_t *)(legacy + 0x50) = *(uint64_t *)(src + 0x50);
    *(uint64_t *)(legacy + 0x58) = *(uint64_t *)(src + 0x58);
    *(uint64_t *)(legacy + 0x60) = *(uint64_t *)(src + 0x60);
    *(uint8_t  *)(legacy + 0x68) = *(uint8_t  *)(src + 0x68);
    *(uint64_t *)(legacy + 0x70) = *(uint64_t *)(src + 0x70);
    *(uint64_t *)(legacy + 0x78) = *(uint64_t *)(src + 0x78);
    *(uint64_t *)(legacy + 0x80) = *(uint64_t *)(src + 0x80);
    *(uint64_t *)(legacy + 0x88) = *(uint64_t *)(src + 0x88);
    *(uint64_t *)(legacy + 0x90) = *(uint64_t *)(src + 0x90);
    *(uint8_t  *)(legacy + 0x98) = *(uint8_t  *)(src + 0x98);

    // Repack shifted fields for Tahoe / Darwin 25
    *(uint64_t *)(legacy + 0xa0) = *(uint64_t *)(src + 0xb0);
    *(uint64_t *)(legacy + 0xa8) = *(uint64_t *)(src + 0xb8);
    *(uint64_t *)(legacy + 0xb0) = *(uint64_t *)(src + 0xc0);
    *(uint32_t *)(legacy + 0xb8) = *(uint32_t *)(src + 0xc8);
    *(uint64_t *)(legacy + 0xc8) = *(uint64_t *)(src + 0xd8);
    *(uint32_t *)(legacy + 0xd0) = *(uint32_t *)(src + 0xe0);
    *(uint32_t *)(legacy + 0xd4) = *(uint32_t *)(src + 0xe4);
    *(uint32_t *)(legacy + 0xd8) = *(uint32_t *)(src + 0xe8);
    *(uint32_t *)(legacy + 0xdc) = *(uint32_t *)(src + 0xec);
    *(uint64_t *)(legacy + 0xe0) = *(uint64_t *)(src + 0xf0);
    *(uint64_t *)(legacy + 0xe8) = *(uint64_t *)(src + 0xf8);
    *(uint64_t *)(legacy + 0xf0) = *(uint64_t *)(src + 0x100);
    *(uint64_t *)(legacy + 0xf8) = *(uint64_t *)(src + 0x108);
    *(uint64_t *)(legacy + 0x100) = *(uint64_t *)(src + 0x110);

    // Extended Tahoe layout mapping
    *(uint64_t *)(legacy + 0x108) = *(uint64_t *)(src + 0x198);
    *(uint64_t *)(legacy + 0x110) = *(uint64_t *)(src + 0x1a0);
    *(uint64_t *)(legacy + 0x118) = *(uint64_t *)(src + 0x1a8);
    *(uint64_t *)(legacy + 0x120) = *(uint64_t *)(src + 0x1b8);
    *(uint64_t *)(legacy + 0x138) = *(uint64_t *)(src + 0x1d0);
    *(uint8_t  *)(legacy + 0x140) = *(uint8_t  *)(src + 0x1d8);
    *(uint32_t *)(legacy + 0x144) = *(uint32_t *)(src + 0x1dc);
    *(uint64_t *)(legacy + 0x148) = *(uint64_t *)(src + 0x1e0);
    *(uint64_t *)(legacy + 0x150) = *(uint64_t *)(src + 0x1e8);
    *(uint64_t *)(legacy + 0x158) = *(uint64_t *)(src + 0x1f0);
    *(uint64_t *)(legacy + 0x160) = *(uint64_t *)(src + 0x1f8);
    *(uint64_t *)(legacy + 0x168) = *(uint64_t *)(src + 0x210);
    *(uint64_t *)(legacy + 0x170) = *(uint64_t *)(src + 0x218);
    *(uint64_t *)(legacy + 0x178) = *(uint64_t *)(src + 0x230);
    *(uint64_t *)(legacy + 0x180) = *(uint64_t *)(src + 0x238);
    *(uint8_t  *)(legacy + 0x188) = *(uint8_t  *)(src + 0x240);
    *(uint8_t  *)(legacy + 0x189) = *(uint8_t  *)(src + 0x241);

    return legacy;
}

static void *fake_compute(id self, SEL _cmd) {
    void *modern = real_compute ? real_compute(self, _cmd) : NULL;
    if (!modern) return NULL;

    void *caller = __builtin_return_address(0);
    if (inDSC(caller)) {
        return modern;
    }

    // Monterey expects 0xa8 bytes for compute descriptor
    char *legacy = (char *)getOrMakeThreadStorage(&threadMontereyComputeOnce, &threadMontereyComputeKey, 0xa8, initComputeKey);
    char *src = (char *)modern;

    *(uint64_t *)(legacy + 0x00) = *(uint64_t *)(src + 0x00);
    *(uint64_t *)(legacy + 0x08) = *(uint64_t *)(src + 0x08);
    *(uint8_t  *)(legacy + 0x10) = *(uint8_t  *)(src + 0x10);
    *(uint16_t *)(legacy + 0x12) = *(uint16_t *)(src + 0x12);
    *(uint64_t *)(legacy + 0x18) = *(uint64_t *)(src + 0x18);
    *(uint64_t *)(legacy + 0x20) = *(uint64_t *)(src + 0x20);
    *(uint64_t *)(legacy + 0x28) = *(uint64_t *)(src + 0x30);
    *(uint64_t *)(legacy + 0x30) = *(uint64_t *)(src + 0x38);
    *(uint64_t *)(legacy + 0x38) = *(uint64_t *)(src + 0x40);
    *(uint64_t *)(legacy + 0x40) = *(uint64_t *)(src + 0x48);
    *(uint8_t  *)(legacy + 0x48) = *(uint8_t  *)(src + 0x50);
    *(uint64_t *)(legacy + 0x50) = *(uint64_t *)(src + 0x68);
    *(uint8_t  *)(legacy + 0x58) = *(uint8_t  *)(src + 0x70);
    *(uint64_t *)(legacy + 0x60) = *(uint64_t *)(src + 0x78);
    *(uint64_t *)(legacy + 0x68) = *(uint64_t *)(src + 0x80);
    *(uint8_t  *)(legacy + 0x70) = *(uint8_t  *)(src + 0x88);

    uint8_t flag = *(uint8_t *)(src + 0x89);
    legacy[0x71] = (legacy[0x71] & ~0x01) | (flag & 0x01);
    legacy[0x71] = (legacy[0x71] & ~0x02) | ((flag << 1) & 0x02);

    *(uint64_t *)(legacy + 0x78) = *(uint64_t *)(src + 0x90);
    *(uint64_t *)(legacy + 0x80) = 0; // Empty dictionary reference
    *(uint64_t *)(legacy + 0x88) = *(uint64_t *)(src + 0x98);
    *(uint64_t *)(legacy + 0x90) = *(uint64_t *)(src + 0xa0);
    *(uint8_t  *)(legacy + 0x98) = *(uint8_t  *)(src + 0xa8);
    *(uint64_t *)(legacy + 0xa0) = *(uint64_t *)(src + 0xb0);

    return legacy;
}

static void swizzleSafer(Class cls, SEL sel, void *fakeImp, void **realImp) {
    if (!cls) return;
    Method m = class_getInstanceMethod(cls, sel);
    if (!m) return;
    *realImp = (void *)method_getImplementation(m);
    method_setImplementation(m, (IMP)fakeImp);
}

__attribute__((constructor))
static void load(void) {
    @autoreleasepool {
        Class renderClass = objc_getClass("MTLRenderPipelineDescriptorInternal");
        if (renderClass) {
            swizzleSafer(renderClass, sel_registerName("_descriptorPrivate"), (void *)fake_render, (void **)&real_render);
        }

        Class computeClass = objc_getClass("MTLComputePipelineDescriptorInternal");
        if (computeClass) {
            swizzleSafer(computeClass, sel_registerName("_descriptorPrivate"), (void *)fake_compute, (void **)&real_compute);
        }
    }
}
