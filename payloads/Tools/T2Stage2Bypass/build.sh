#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/build"
SRC_DIR="${SCRIPT_DIR}/src"

echo "==========================================================="
echo "Building T2 Stage 2 Bypass (macOS Tahoe OCLP)"
echo "==========================================================="

mkdir -p "${BUILD_DIR}"

# 1. Build the dynamic library interposer
echo "[1/3] Compiling libT2Stage2Bypass.dylib..."
clang -O2 -dynamiclib \
    -arch x86_64 \
    -framework Foundation \
    -install_name "@rpath/libT2Stage2Bypass.dylib" \
    -o "${BUILD_DIR}/libT2Stage2Bypass.dylib" \
    "${SRC_DIR}/T2Stage2Bypass.m"

echo "      Created: ${BUILD_DIR}/libT2Stage2Bypass.dylib"

# 2. Build the test harness
echo "[2/3] Compiling test_harness executable..."
clang -O2 \
    -arch x86_64 \
    -framework Foundation \
    -F/System/Library/PrivateFrameworks \
    -framework OSInstaller \
    -o "${BUILD_DIR}/test_harness" \
    "${SRC_DIR}/test_harness.m"

echo "      Created: ${BUILD_DIR}/test_harness"

# 3. Execute Verification Test
echo "[3/3] Running verification test with DYLD_INSERT_LIBRARIES..."
echo "-----------------------------------------------------------"
DYLD_INSERT_LIBRARIES="${BUILD_DIR}/libT2Stage2Bypass.dylib" "${BUILD_DIR}/test_harness"

echo "==========================================================="
echo "BUILD & VERIFICATION SUCCESSFUL!"
echo "==========================================================="
