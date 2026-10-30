#!/usr/bin/env python3
"""
PyInstaller Entry Point - Hardened with Full Extraction Fix
"""
import sys
import logging

# SECURITY FIX: Remove the current directory from the search path.
if "" in sys.path:
    sys.path.remove("")

# We configure logging to write to sys.stdout (the Terminal window)
logging.basicConfig(
    level=logging.ERROR,
    format='%(message)s', # Keep it clean for the Terminal
    stream=sys.stdout
)

from bridge_patcher import main

if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print("\n" + "="*60)
        logging.error("Whoops, the app crashed because of the following error:")
        print(f"Direct Error: {e}")
        print("-" * 60)
        logging.exception("Stack Trace:")
        print("="*60)
        input("\nPress ENTER to close this window...")
        sys.exit(3)
