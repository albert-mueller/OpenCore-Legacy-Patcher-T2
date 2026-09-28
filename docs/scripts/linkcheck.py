import shutil
import subprocess
from pathlib import Path

# Define folders you want to skip entirely
IGNORED_DIRS = {"node_modules", "_book", ".git"}

# Find all markdown files, filtering out ignored directories at any level
md_files = [
    p for p in Path().resolve().glob("**/*.md")
    if not any(ignored in p.parts for ignored in IGNORED_DIRS)
]

# Dynamically locate npx to prevent FileNotFoundError in GUI environments/Git hooks
npx_path = shutil.which("npx") or "npx"

for file_path in md_files:
    # Pass arguments as a list (shell=False) to ensure security.
    # Set cwd to file_path.parent so relative markdown links resolve correctly.
    result = subprocess.run(
        [npx_path, "markdown-link-check", file_path.name],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,  # Automatically handles string decoding and newline translation
        cwd=file_path.parent
    )

    # Split output by lines (Universal Newlines mode handled by text=True)
    lines = result.stdout.splitlines()

    # Filter lines to show only files, structural statuses, and strip out 429 rate limits
    filtered_lines = [
        line for line in lines
        if ("FILE: " in line or " → Status: " in line) and " → Status: 429" not in line
    ]

    # Print the clean output
    for line in filtered_lines:
        print(line)
