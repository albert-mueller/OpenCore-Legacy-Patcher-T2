#!/usr/bin/env python3
"""
PyInstaller Entry Point - Hardened
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

from opencore_legacy_patcher import main

if __name__ == '__main__':
    try:
        # Normal launch attempt
        main()
    except Exception as e:
        # THIS ONLY PRINTS TO TERMINAL IF THE APP FAILS
        print("\n" + "="*60)
        
        # 1. human-friendly error
        logging.error("Whoops, the app crashed because of the following error:")
        print(f"Direct Error: {e}")
        
        print("-" * 60)
        
        # 2. This prints the full technical log (Stack Trace) to the Terminal
        logging.exception("Stack Trace:")
        
        print("="*60)
        
        # 3. Keep the Terminal window open so the tester can copy the text
        input("\nPress ENTER to close this window...")
        sys.exit(3)
