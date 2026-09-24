import logging
import plistlib
from pathlib import Path
from typing import Tuple, Optional

class ParseCommitInfo:
    def __init__(self, binary_path: str) -> None:
        self.binary_path = Path(binary_path)
        self.plist_path = self._resolve_plist_path()

    def _resolve_plist_path(self) -> Optional[Path]:
        # Suche im selben Verzeichnis wie die Binärdatei oder im übergeordneten "Resources"-Ordner
        # Anstatt hartem .replace() suchen wir nach einer Info.plist in der Nähe
        possible_paths = [
            self.binary_path.parent.parent / "Info.plist",
            self.binary_path.parent / "Info.plist"
        ]
        for p in possible_paths:
            if p.exists():
                return p
        return None

    def generate_commit_info(self) -> Tuple[str, str, str]:
        if self.plist_path and self.plist_path.exists():
            try:
                with self.plist_path.open("rb") as f:
                    plist_info = plistlib.load(f)
                    github_data = plist_info.get("Github", {})

                    return (
                        github_data.get("Branch", "Unknown"),
                        github_data.get("Commit Date", "Unknown"),
                        github_data.get("Commit URL", ""),
                    )
            except (plistlib.InvalidFileException, OSError):
                logging.error("We couldn't identify the commit information.")
                logging.exception("Stack Trace:")
                return ("Error determining commit information", "Not applicable", "") # anstatt ein reines pass-Argument, die erlaubt Angreifern, beliebiges Code auszuführen, ist return und dann Fehler zurückgegeben
        # behebt eine Sicherheitslücke, die erlaubt Angreifern zu behaupten, dass den Code von "Source" läufe, auch wenn if self.plist_path and self.plist_path.exists(): erfolgreich gelaufen hat
        else:
            return ("Running from source", "Not applicable", "")
