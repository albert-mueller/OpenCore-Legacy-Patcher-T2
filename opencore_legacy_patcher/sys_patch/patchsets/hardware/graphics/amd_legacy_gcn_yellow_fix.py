# amd_legacy_gcn_yellow_fix.py
"""
Fix for AMD Legacy GCN yellow screen / gamma LUT issue on macOS 26 Tahoe.
"""

from ...base import PatchType

patch = {
    "AMD Legacy GCN Tahoe Color Fix": {
        PatchType.EXECUTE: {
            "/usr/bin/defaults write /Library/Preferences/.GlobalPreferences.plist AppleColorSyncLinearGamma -bool true": True,
        },
    }
}
