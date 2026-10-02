#!/bin/bash

# Script to build the metal_31001_interposer and inject it into the target driver.
# Usage: ./build_and_patch.sh /path/to/original/AppleIntelSKLGraphicsMTLDriver

if [ -z "$1" ]; then
    echo "Usage: $0 /path/to/original/AppleIntelSKLGraphicsMTLDriver"
    exit 1
fi

TARGET_DRIVER="$1"
PATCHED_DRIVER="AppleIntelSKLGraphicsMTLDriver_patched"
DYLIB_NAME="metal_31001_interposer.dylib"
INSERT_DYLIB="/tmp/insert_dylib/build/Release/insert_dylib"

if [ ! -f "$INSERT_DYLIB" ]; then
    echo "Error: insert_dylib not found at $INSERT_DYLIB."
    echo "Please compile it first or adjust the path."
    exit 1
fi

echo "[*] Compiling $DYLIB_NAME..."
clang -dynamiclib -arch x86_64 -framework Foundation -framework Metal -o "$DYLIB_NAME" metal_31001_interposer.m
if [ $? -ne 0 ]; then
    echo "[-] Failed to compile $DYLIB_NAME"
    exit 1
fi
echo "[+] Compiled successfully."

echo "[*] Copying original driver to $PATCHED_DRIVER..."
cp "$TARGET_DRIVER" "$PATCHED_DRIVER"

echo "[*] Injecting load command into $PATCHED_DRIVER..."
# We use @loader_path so the dylib can be placed in the same MacOS folder as the driver
"$INSERT_DYLIB" --inplace --all-yes "@loader_path/$DYLIB_NAME" "$PATCHED_DRIVER"

echo "[*] Ad-hoc signing the patched driver..."
codesign -f -s - "$PATCHED_DRIVER"

echo "[+] Done! You can now place $PATCHED_DRIVER (rename it to AppleIntelSKLGraphicsMTLDriver) and $DYLIB_NAME inside the driver's MacOS folder in your OCLP payloads."
