#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Installing BridgePatcher for MacBookPro14,3..."
# BridgePatcher.pkg is the current name; OpenCore-Patcher-T2.pkg for older downloads.
PKG="${DIR}/BridgePatcher.pkg"
[ -f "$PKG" ] || PKG="${DIR}/OpenCore-Patcher-T2.pkg"
sudo installer -pkg "$PKG" -target /
