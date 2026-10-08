CC=clang
OUTPUT=com.albert-mueller.opencore-patcher-t2.privileged-helper

# Optional, release builds only:
#   make CERT_SHA1=<40 hex chars>        also hard-code the expected leaf certificate
#   make CLIENT_ID=<bundle identifier>   if the app's bundle identifier was changed
#   make ARCHS="-arch x86_64 -arch arm64" force a Universal build (e.g. Catalina + Xcode 12.2+)
#   make ARCHS="-arch x86_64"            force an Intel-only build
#
# Default architectures depend on the build host: before macOS 11 Big Sur the
# toolchain (Xcode/Command Line Tools <= 11, e.g. on High Sierra) has no arm64
# support and fails with "Unsupported architecture" in <sys/cdefs.h>, so build
# Intel-only there. 10.16 is Big Sur reporting itself in compatibility mode.
HOST_PRE_BIG_SUR := $(shell sw_vers -productVersion 2>/dev/null | awk -F. '$$1 == 10 && $$2 < 16 { print "yes" }')
ifeq ($(HOST_PRE_BIG_SUR),yes)
ARCHS ?= -arch x86_64
else
ARCHS ?= -arch x86_64 -arch arm64
endif
# Compile and link against the SDK instead of the running system. Without a
# sysroot, older Command Line Tools (e.g. 10.x on High Sierra) link against the
# .tbd stubs in /System/Library/Frameworks, which fall out of sync with the real
# libraries after OS updates ("text-based stub file ... are out of sync"). Newer
# toolchains already use this SDK by default. Override with make SDKROOT=<path>.
SDKROOT ?= $(shell xcrun --sdk macosx --show-sdk-path 2>/dev/null)
SYSROOT_FLAGS=
ifneq ($(wildcard $(SDKROOT)/SDKSettings.plist),)
SYSROOT_FLAGS= -isysroot $(SDKROOT)
endif
# Same minimum as the app (LSMinimumSystemVersion in OpenCore-Patcher-GUI.spec).
# The arm64 slice is raised to macOS 11.0 by clang automatically.
MACOS_MIN ?= 10.13.6
PIN_FLAGS=
ifneq ($(CERT_SHA1),)
PIN_FLAGS+= -DOCLP_PINNED_LEAF_SHA1=\"$(CERT_SHA1)\"
endif
ifneq ($(CLIENT_ID),)
PIN_FLAGS+= -DOCLP_CLIENT_IDENTIFIER=\"$(CLIENT_ID)\"
endif

all: clean release

release: main.m
	$(CC) $(SYSROOT_FLAGS) -framework Foundation -framework Security $(ARCHS) -mmacosx-version-min=$(MACOS_MIN) $(PIN_FLAGS) -o $(OUTPUT) main.m

debug: main.m
	$(CC) $(SYSROOT_FLAGS) -framework Foundation -framework Security $(ARCHS) -mmacosx-version-min=$(MACOS_MIN) -o $(OUTPUT) main.m -DDEBUG

clean:
	/bin/rm -f $(OUTPUT)
