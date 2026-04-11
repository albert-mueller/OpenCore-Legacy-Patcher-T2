#!/usr/bin/env python3
"""
PyInstaller Entry Point - Hardened
"""
import sys

# SECURITY FIX: Remove the current directory from the search path.
# This prevents the script from accidentally loading malicious local files.
if "" in sys.path:
    sys.path.remove("")

from opencore_legacy_patcher import main

if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Avoid printing 'E' to prevent leaking internal path info
        print("The application has issues to launch. Please report this issue.")
        sys.exit(1)
