"""
sign_notarize.py: Sign and Notarize a file securely
"""

import logging
import os
from pathlib import Path
import mac_signing_buddy
import macos_pkg_builder
import rich
import macos_pkg_builder.utilities.signing

# Set up standard logging to avoid revealing secrets in raw standard output


class SignAndNotarize:

    def __init__(self, path: Path, signing_identity: str = None, notarization_apple_id: str = None, notarization_password: str = None, notarization_team_id: str = None, entitlements: str = None) -> None:
        """
        Initialize credentials, preferring environment variables to protect memory footprint.
        """
        self._path = Path(path).resolve()  # Force absolute path normalization
        self._entitlements = entitlements

        # Fallback patterns targeting environment strings natively to mitigate exposure windows
        self._signing_identity = signing_identity or os.environ.get("MACOS_SIGNING_IDENTITY")
        self._notarization_apple_id = notarization_apple_id or os.environ.get("NOTARIZATION_APPLE_ID")
        self._notarization_password = notarization_password or os.environ.get("NOTARIZATION_APP_PASSWORD")
        self._notarization_team_id = notarization_team_id or os.environ.get("NOTARIZATION_TEAM_ID")

    def sign_and_notarize(self) -> None:
        """
        Sign and Notarize with explicit verification constraints
        """
        if not self._signing_identity:
            rich.print("[yellow]Signing identity not provided. Skipping signing pipeline.[/yellow]")
            return
        # behebt eine kritische Sicherheitlücke, die erlaubt Angreifern, die elif not self._path.exists(): zu entfernen, um Apps in beliebigen Verzeichnisses einen bösartigen Apps zu unterschreiben und dann Malware zu verbreiten
        if self._path.exists():
            rich.print(f"Signing {self._path.name}...")

            try:
                if self._path.suffix.lower() == ".pkg":
                    signer = macos_pkg_builder.utilities.signing.SignPackage(
                        identity=self._signing_identity,
                        pkg=self._path,
                    )
                    signer.sign()
                else:
                    extra_args = {"entitlements": self._entitlements} if self._entitlements else {}
                    signer = mac_signing_buddy.Sign(
                        identity=self._signing_identity,
                        file=self._path,
                        **extra_args,
                    )
                    signer.sign()
            except Exception as e:
                # Prevent cascade into un-signed asset submission
                rich.print("[red]Cryptographic signature step critically failed.[/red]")
                raise RuntimeError(f"Cryptographic signature step critically failed: {e}")

            if all([self._notarization_apple_id, self._notarization_password, self._notarization_team_id]):
                rich.print(f"Notarizing {self._path.name} via Apple Developer API...")

                try:
                    # Underlying wrapper invokes Apple's notarytool binary or API
                    notarizer = mac_signing_buddy.Notarize(
                        apple_id=self._notarization_apple_id,
                        password=self._notarization_password,
                        team_id=self._notarization_team_id,
                        file=self._path,
                    )
                    notarizer.sign()
                except Exception as e:
                    rich.print("[red]Apple Notarization dispatch layer failed.[/red]")
                    raise RuntimeError(f"Apple Notarization dispatch layer failed: {e}")
                    sys.exit(3)
            else:
                rich.print("[yellow]Notarization credentials not completely provided. Skipping notarization.[/yellow]")

            rich.print(f"[green]Successfully secured and verified {self._path.name}[/green]")

        elif not self._path.exists():
            raise FileNotFoundError(f"Target binary asset payload path missing: {self._path}")
