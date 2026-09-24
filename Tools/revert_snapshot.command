#!/usr/bin/env bash
# revert_snapshot.command
# Dynamically detects the system layout and assists in reverting APFS snapshots securely.

echo "================================================="
echo "   APFS Snapshot Reversion Tool (Diagnostics)    "
echo "================================================="
echo

# 1. Detect OS
echo "=== System Information ==="
OS_VER=$(sw_vers -productVersion)
OS_BUILD=$(sw_vers -buildVersion)
echo "macOS Version: $OS_VER ($OS_BUILD)"
echo

if [[ "$OS_VER" == 13.* ]]; then
    echo "ERROR: macOS Ventura (13.x) detected."
    echo "This tool must NOT be used to modify Ventura snapshots!"
    echo "Aborting."
    read -p "Press Enter to exit..."
    exit 1
fi

# 2. Detect System Volume and APFS Container
echo "=== Detecting Volumes ==="
SYS_DISK=$(diskutil info / | grep "Device Identifier" | awk '{print $3}')
if [ -z "$SYS_DISK" ]; then
    echo "ERROR: Could not determine System Volume identifier."
    read -p "Press Enter to exit..."
    exit 1
fi

APFS_CONTAINER=$(diskutil info / | grep "Part of Whole" | awk '{print $4}')
echo "System Volume: $SYS_DISK"
echo "APFS Container: $APFS_CONTAINER"

# Detect Data and Preboot
echo "Querying Container for Data and Preboot volumes..."
DATA_VOL=$(diskutil list "$APFS_CONTAINER" | grep "Data" | awk '{print $NF}')
PREBOOT_VOL=$(diskutil list "$APFS_CONTAINER" | grep "Preboot" | awk '{print $NF}')
echo "Data Volume: $DATA_VOL"
echo "Preboot Volume: $PREBOOT_VOL"
echo

# 3. Detect Snapshots
echo "=== Available Snapshots on / ==="
diskutil apfs listSnapshots / || echo "Could not list snapshots without sudo or no snapshots available."
echo

# 4. Action
echo "=== Revert Action ==="
echo "To revert to the last sealed snapshot, the following command must be run with root privileges:"
echo
echo "  sudo bless --mount / --bootefi --last-sealed-snapshot"
echo
read -p "TYPE YES TO CONTINUE: " USER_INPUT

if [ "$USER_INPUT" == "YES" ]; then
    echo "Requesting sudo privileges to execute bless command..."
    sudo bless --mount / --bootefi --last-sealed-snapshot
    if [ $? -eq 0 ]; then
        echo "Successfully blessed the last sealed snapshot."
        echo "Please reboot your system for changes to take effect."
    else
        echo "ERROR: Failed to bless the snapshot."
    fi
else
    echo "Operation cancelled by user."
fi

echo
read -p "Press Enter to exit..."
