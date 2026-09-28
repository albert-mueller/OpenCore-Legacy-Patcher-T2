#!/bin/bash
# =============================================================================
# backup_state.command
# READ-ONLY — Dumps system state for debugging purposes.
# =============================================================================
# Safety: This script does NOT modify any system files, NVRAM, snapshots, or EFI.
# All output is written to a timestamped directory in Backups/
# =============================================================================

set -euo pipefail

# Find script directory and target backup directory
SCRIPT_DIR="$(dirname "$0")"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
TIMESTAMP=$(date "+%Y-%m-%d-%H-%M-%S")
BACKUP_DIR="${PROJECT_DIR}/Backups/${TIMESTAMP}"

echo "============================================="
echo "  OCLP T1 — Backup State Tool"
echo "============================================="
echo "Generating state dump..."
echo ""

mkdir -p "$BACKUP_DIR"

echo ">>> Collecting diskutil list..."
diskutil list > "$BACKUP_DIR/diskutil_list.txt" 2>&1

echo ">>> Collecting diskutil apfs list..."
diskutil apfs list > "$BACKUP_DIR/diskutil_apfs_list.txt" 2>&1

echo ">>> Collecting system version and build..."
sw_vers > "$BACKUP_DIR/sw_vers.txt" 2>&1
uname -a > "$BACKUP_DIR/uname.txt" 2>&1

echo ">>> Collecting mounted volumes..."
mount > "$BACKUP_DIR/mount.txt" 2>&1

echo ">>> Collecting boot-args..."
nvram boot-args 2>/dev/null > "$BACKUP_DIR/boot_args.txt" 2>&1 || echo "No boot-args set" > "$BACKUP_DIR/boot_args.txt"

# If an EFI volume is currently mounted, backup its structure
if [ -d "/Volumes/EFI/EFI" ]; then
    echo ">>> Collecting mounted EFI structure..."
    ls -laR /Volumes/EFI/ > "$BACKUP_DIR/efi_structure.txt" 2>&1 || true
fi

echo "============================================="
echo "Backup saved to: $BACKUP_DIR"
echo "============================================="
