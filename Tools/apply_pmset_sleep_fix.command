#!/bin/bash
# =============================================================================
# apply_pmset_sleep_fix.command
# LEVEL 1 FIX: Disable deep standby, hibernation and darkwake, keeping state in RAM
# =============================================================================
# 100% reversible and safe macOS-level changes (no EFI modifications)
# =============================================================================

set -euo pipefail

echo "============================================================"
echo "  LEVEL 1 FIX: macOS Sleep/Wake optimization for MBP14,3"
echo "============================================================"
echo ""
echo "This script will configure macOS power management to:"
echo " 1. Keep the active state in RAM only (hibernatemode 0)"
echo " 2. Avoid PCIe deep sleep / D3cold on the GPUs (standby 0)"
echo " 3. Disable autopoweroff (autopoweroff 0)"
echo " 4. Disable silent background wakes (powernap 0, proximitywake 0)"
echo ""
echo "Requesting administrator privileges:"
sudo -v

echo "Applying pmset configuration..."
sudo pmset -a hibernatemode 0
sudo pmset -a standby 0
sudo pmset -a autopoweroff 0
sudo pmset -a powernap 0
sudo pmset -a proximitywake 0

# Remove the old sleepimage to free up disk space (optional and safe)
if [ -f /var/vm/sleepimage ]; then
    echo "Cleaning up the old /var/vm/sleepimage..."
    sudo rm -f /var/vm/sleepimage || true
fi

echo ""
echo "============================================================"
echo "  CURRENT CONFIGURATION (pmset -g)"
echo "============================================================"
pmset -g

echo ""
echo ">> LEVEL 1 FIX APPLIED SUCCESSFULLY!"
echo "You can now test closing the lid or sleeping from the Apple menu."
echo ""
