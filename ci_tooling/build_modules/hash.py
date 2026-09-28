"""
hash.py: creates a hash for the PKGs using hashlib and saves them to certificates.txt
"""

import hashlib
import rich
import shutil
from pathlib import Path


class GenerateHash():
    def __init__(self):
        shutil.copyfile("./ci_tooling/privileged_helper_tool/com.albert-mueller.opencore-patcher-t2.privileged-helper", "./dist/com.albert-mueller.opencore-patcher-t2.privileged-helper")
        Path("./dist/certificates.txt").unlink(missing_ok=True)
        self.start()
        Path("./dist/com.albert-mueller.opencore-patcher-t2.privileged-helper").unlink(missing_ok=True)

    def start(self):
        file_dump = ["Resource certificates:"]
        dist = Path("./dist/")

        for filepath in list(dist.glob("*.pkg")) + list(dist.glob("*.privileged-helper")):

            sha256 = hashlib.sha256()

            with open(filepath, "rb") as f:
                while chunk := f.read(8192):
                    sha256.update(chunk)

            file_dump.append(f"{filepath.name}: {sha256.hexdigest()}")
            rich.print(f"{filepath.name}: {sha256.hexdigest()}")

        with open("./dist/certificates.txt", "w") as f:
            f.write("\n".join(file_dump))
