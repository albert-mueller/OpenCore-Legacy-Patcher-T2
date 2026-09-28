#!/bin/bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Installing OpenCore Legacy Patcher T1 for MacBookPro14,3..."
sudo installer -pkg "${DIR}/OpenCore-Patcher-T2.pkg" -target /
