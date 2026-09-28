#!/bin/bash
# =============================================================================
# kdk_remove.command
# Interactive tool to remove installed Kernel Debug Kits
# =============================================================================

set -euo pipefail

echo "============================================="
echo "  OCLP T1 — KDK Removal Tool"
echo "============================================="

KDK_DIR="/Library/Developer/KDKs"

if [ ! -d "$KDK_DIR" ]; then
    echo "No KDK directory found ($KDK_DIR)."
    echo "No KDKs installed."
    exit 0
fi

# Find all KDKs
KDK_FILES=$(find "$KDK_DIR" -mindepth 1 -maxdepth 1 -type d)

if [ -z "$KDK_FILES" ]; then
    echo "No KDKs installed in $KDK_DIR."
    exit 0
fi

echo "Installed KDKs found:"
echo ""

# Display each KDK with its size
while IFS= read -r kdk_path; do
    kdk_name=$(basename "$kdk_path")
    kdk_size=$(du -sh "$kdk_path" 2>/dev/null | awk '{print $1}')
    echo "- $kdk_name ($kdk_size)"
    echo "  Path: $kdk_path"
    echo ""
done <<< "$KDK_FILES"

echo "WARNING: This operation will delete ALL of the KDKs listed above."
echo "This operation requires administrator privileges."
echo ""
read -p "TYPE YES TO CONTINUE: " confirm

if [ "$confirm" != "YES" ]; then
    echo "Operation cancelled."
    exit 1
fi

echo "Removing..."
for kdk_path in $KDK_FILES; do
    echo "Removing: $kdk_path"
    sudo rm -rf "$kdk_path"
done

echo "All KDKs have been removed."
echo "============================================="
