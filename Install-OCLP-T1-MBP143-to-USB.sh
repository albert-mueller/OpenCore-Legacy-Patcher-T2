#!/bin/bash
# Install-OCLP-T1-MBP143-to-USB.sh
# Terminal interactive installer for macOS Tahoe

echo "=================================================="
echo "OCLP T1 MBP14,3 — USB EFI INSTALLER"
echo "=================================================="



echo "Checking for the OCLP-MBP143 target..."

# Find the external USB drive named OCLP-MBP143
TARGET_DISK=$(diskutil list | grep "OCLP-MBP143" | awk '{print $NF}' | head -n 1)
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

EFI_PARTITION=$(diskutil list "$PARENT_DISK" | grep "EFI" | awk '{print $NF}' | head -n 1)
if [ -z "$EFI_PARTITION" ]; then
    echo "CRITICAL ERROR: Could not find the EFI partition on disk $PARENT_DISK"
    exit 1
fi

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

read -p "Proceed with the installation on $PARENT_DISK and $EFI_PARTITION? (y/n) " -n 1 -r
echo ""
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    echo "Operation cancelled by the user."
    exit 1
fi

echo "=================================================="
echo "STAGE 1: PREPARING DATA ON THE MAIN PARTITION"
echo "=================================================="
MAIN_VOL="/Volumes/OCLP-MBP143"

if [ ! -d "$MAIN_VOL" ]; then
    echo "Waiting for $MAIN_VOL to mount..."
    diskutil mount "$TARGET_DISK"
fi

if [ ! -d "$MAIN_VOL" ]; then
    echo "ERROR: Could not mount the main OCLP-MBP143 volume."
    exit 1
fi

echo "Creating support folders..."
mkdir -p "$MAIN_VOL/Builds/Standard-Build"
mkdir -p "$MAIN_VOL/Builds/TEST-B-Build"
mkdir -p "$MAIN_VOL/Tools"
mkdir -p "$MAIN_VOL/Backups"
mkdir -p "$MAIN_VOL/Diagnostics"
mkdir -p "$MAIN_VOL/Documentation"

SRC_DIR="$(dirname "$0")"

echo "Copying tools and reports..."
if [ -d "$SRC_DIR/Tools" ]; then
    cp -R "$SRC_DIR/Tools/"* "$MAIN_VOL/Tools/"
fi
if [ -d "$SRC_DIR/Build-Folder" ]; then
    # Copy builds if they exist in Build-Folder
    cp -R "$SRC_DIR/Build-Folder/"* "$MAIN_VOL/Builds/" 2>/dev/null || true
fi

echo ""
echo "=================================================="
echo "STAGE 2: EFI INSTALLATION"
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

EFI_SRC_DIR="$SRC_DIR/Build-Folder/Standard-Build/EFI"
if [ ! -d "$EFI_SRC_DIR" ]; then
    EFI_SRC_DIR="$SRC_DIR/Build-Folder/TEST-B-Build/EFI"
fi
# Fallback to general if not using specific
if [ ! -d "$EFI_SRC_DIR" ]; then
    EFI_SRC_DIR="$SRC_DIR/EFI"
fi

if [ ! -d "$EFI_SRC_DIR" ]; then
    echo "ERROR: Could not find the source EFI folder in $EFI_SRC_DIR."
    sleep 2
    diskutil unmount force "$EFI_PARTITION"
    exit 1
fi

echo "Cleaning up the existing EFI..."
rm -rf /Volumes/EFI/EFI
rm -rf /Volumes/EFI/System

echo "Copying the EFI from $EFI_SRC_DIR..."
cp -R "$EFI_SRC_DIR" /Volumes/EFI/

if [ ! -f "/Volumes/EFI/EFI/OC/config.plist" ]; then
    echo "ERROR: Installation failed. config.plist is missing from the EFI."
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
