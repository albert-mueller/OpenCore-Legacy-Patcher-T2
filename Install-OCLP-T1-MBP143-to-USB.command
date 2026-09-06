#!/bin/bash
# Install-OCLP-T1-MBP143-to-USB.command
# Interactive GUI installer for macOS Tahoe

echo "=================================================="
echo "OCLP T1 MBP14,3 — USB EFI INSTALLER"
echo "=================================================="

# Elevate privileges
if [ "$EUID" -ne 0 ]; then
  echo "Requesting administrator privileges..."
  sudo "$0" "$@"
  exit $?
fi

echo "Checking for the OCLP-MBP143 target..."

# Find the external USB drive named OCLP-MBP143
TARGET_DISK=$(diskutil list -plist | grep -A 10 "OCLP-MBP143" | grep "DeviceIdentifier" -A 1 | tail -n 1 | sed -e 's/.*<string>//' -e 's/<\/string>.*//')
if [ -z "$TARGET_DISK" ]; then
    echo "ERROR: Could not find a volume named OCLP-MBP143."
    exit 1
fi

PARENT_DISK=$(diskutil info "$TARGET_DISK" | grep "Part of Whole" | awk '{print $4}')
if [ -z "$PARENT_DISK" ]; then
    echo "ERROR: Could not determine the parent disk for $TARGET_DISK."
    exit 1
fi

# Ensure it's not disk0, disk1, or disk2
if [[ "$PARENT_DISK" == "disk0" || "$PARENT_DISK" == "disk1" || "$PARENT_DISK" == "disk2" ]]; then
    echo "CRITICAL ERROR: The target is on a system disk ($PARENT_DISK). Aborting for safety."
    exit 1
fi

# Ensure it's external
IS_EXTERNAL=$(diskutil info "$PARENT_DISK" | grep "Device Location" | grep -c "External")
if [ "$IS_EXTERNAL" -eq 0 ]; then
    echo "CRITICAL ERROR: Disk $PARENT_DISK is not an external device!"
    exit 1
fi

EFI_PARTITION="${PARENT_DISK}s1"

echo ""
echo "Target: External USB ($PARENT_DISK)"
echo "Volume: OCLP-MBP143"
echo "EFI: $EFI_PARTITION"
echo "Model: MacBookPro14,3"
echo "TEST-B: ENABLED"
echo "WhateverGreen: 1.7.0"
echo "-wegnoegpu: ENABLED"
echo "T1: ENABLED"
echo "Wi-Fi: 14E4:43BA"
echo "Country: IT"
echo "=================================================="

echo "Mounting EFI..."
diskutil mount "$EFI_PARTITION" || {
    echo "The EFI partition is unformatted or damaged. Formatting..."
    newfs_msdos -v EFI -F 32 /dev/r$EFI_PARTITION
    diskutil mount "$EFI_PARTITION"
}

if [ ! -d "/Volumes/EFI" ]; then
    echo "ERROR: Failed to mount the EFI partition."
    exit 1
fi

SRC_DIR="$(dirname "$0")/EFI"
if [ ! -d "$SRC_DIR" ]; then
    echo "ERROR: Could not find the source EFI folder in $SRC_DIR."
    sleep 2
    diskutil unmount force "$EFI_PARTITION"
    exit 1
fi

echo "Cleaning up the existing EFI..."
rm -rf /Volumes/EFI/EFI
rm -rf /Volumes/EFI/System

echo "Copying the TEST-B EFI..."
cp -R "$SRC_DIR" /Volumes/EFI/

if [ ! -f "/Volumes/EFI/EFI/OC/Kexts/WhateverGreen.kext/Contents/Info.plist" ]; then
    echo "ERROR: WhateverGreen.kext verification failed!"
    sleep 2
    diskutil unmount force "$EFI_PARTITION"
    exit 1
fi

grep -q "-wegnoegpu" "/Volumes/EFI/EFI/OC/config.plist"
if [ $? -ne 0 ]; then
    echo "ERROR: -wegnoegpu not found in config.plist!"
    sleep 2
    diskutil unmount force "$EFI_PARTITION"
    exit 1
fi

CONFIG_HASH=$(shasum -a 256 "/Volumes/EFI/EFI/OC/config.plist" | awk '{print $1}')
echo "CONFIG SHA256: $CONFIG_HASH"
echo "EFI installation completed successfully!"
echo "Unmounting EFI..."
sleep 2
diskutil unmount force "$EFI_PARTITION"

echo "=================================================="
echo "OPERATION COMPLETE."
echo "You can now reboot while holding Option (Alt)."
echo "=================================================="
