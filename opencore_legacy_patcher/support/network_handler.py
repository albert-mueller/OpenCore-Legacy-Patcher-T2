"""
network_handler.py: Library dedicated to Network Handling tasks including downloading files
Refactored: Thread-safe, non-blocking shutdown, and full diagnostic logging preserved.
"""

import time
import requests
import logging
import enum
import hashlib
from typing import Optional, Union
from pathlib import Path
from . import utilities

SESSION = requests.Session()

class DownloadStatus(enum.Enum):
    INACTIVE:    str = "Inactive"
    DOWNLOADING: str = "Downloading"
    ERROR:       str = "Error"
    COMPLETE:    str = "Complete"

class NetworkUtilities:
    def __init__(self, url: str = None) -> None:
        self.url = url or "https://github.com"

    def verify_network_connection(self) -> bool:
        try:
            requests.head(self.url, timeout=5, allow_redirects=True)
            return True
        except (requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, 
                requests.exceptions.ConnectionError, requests.exceptions.HTTPError):
            return False

    def validate_link(self) -> bool:
        try:
            response = SESSION.head(self.url, timeout=5, allow_redirects=True)
            response.raise_for_status()
            return True
        except (requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, 
                requests.exceptions.ConnectionError, requests.exceptions.HTTPError):
            return False

    def get(self, url: str, **kwargs) -> requests.Response:
        try:
            return SESSION.get(url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, 
                requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as error:
            logging.warning(f"Error calling requests.get: {error}")
            return requests.Response()

    def post(self, url: str, **kwargs) -> requests.Response:
        try:
            return SESSION.post(url, **kwargs)
        except (requests.exceptions.Timeout, requests.exceptions.TooManyRedirects, 
                requests.exceptions.ConnectionError, requests.exceptions.HTTPError) as error:
            logging.warning(f"Error calling requests.post: {error}")
            return requests.Response()

class DownloadObject:
    # A single stalled socket read (common on swcdn.apple.com over long
    # multi-GB transfers) should not discard an otherwise-healthy download.
    # These control how many times a transient network error is retried,
    # resuming via HTTP Range instead of restarting from byte 0.
    MAX_DOWNLOAD_RETRIES = 5
    RETRY_BACKOFF_BASE_SECONDS = 3

    def __init__(self, url: str, path: str, checksum_algo: Optional["hashlib._Hash"] = None) -> None:
        try:
            self.url = url
            self.status = DownloadStatus.INACTIVE
            self.error_msg = ""
            self.filename = self._get_filename()
            self.filepath = Path(path)
            self.total_file_size = 0.0
            self.downloaded_file_size = 0.0
            self.start_time = time.time()
            self.error = False
            self.should_stop = False
            self.download_complete = False
            self.has_network = NetworkUtilities(self.url).verify_network_connection()
            self._checksum_storage = checksum_algo
            self.checksum = None
            if self.has_network:
                self._populate_file_size()
        except Exception as e:
            logging.error("Wir haben einen Problem, einige Aktualisierungsparametern zu setzen")
            logging.error("We have an issue to set some update parameters")
            logging.exception("Stack Trace:")
            logging.info("Bitte suchen Sie manuell nach Updates.")
            logging.info("Please check for updates manually.")

    
    # --- RESTORED DIAGNOSTIC/HELPER METHODS ---
    def _get_filename(self) -> str:
        """
        Get the filename from the URL

        Returns:
            str: Filename
        """
        # Diagnostic: Log the result to ensure URL parsing isn't failing 
        # due to unexpected URL structures
        filename = Path(self.url).name
        logging.debug(f"Resolved filename from URL: {filename}")
        return filename
    
    def _populate_file_size(self) -> None:
        """
        Get the file size of the file to be downloaded

        If unable to get file size, set to zero.
        Uses a HEAD request to identify the Content-Length header.
        """
        logging.info("Probieren, zu ermitteln der Datei-Größe für: {self.url}")
        logging.debug(f"Attempting to determine file size for: {self.url}")
        
        try:
            # We use SESSION (global) for consistency with your original code
            # Timeout is strictly defined to prevent hanging during the check.
            # 10s (rather than the previous 5s) gives a HEAD + cross-host redirect
            # (e.g. github.com -> release-assets.githubusercontent.com) more slack
            # on higher-latency or slower links before we give up on a real size.
            result = SESSION.head(self.url, allow_redirects=True, timeout=10)
            
            if 'Content-Length' in result.headers:
                self.total_file_size = float(result.headers['Content-Length'])
                logging.info(f"Datei-Größe bestätigt: {self.total_file_size} bytes")
                logging.info(f"File size confirmed: {self.total_file_size} bytes")
            else:
                # This provides the diagnostic insight you need—did the server 
                # actually return a length or is it missing?
                logging.warning(f"Content-Length-Header fehlt für {self.url}")
                logging.warning(f"Content-Length header missing for {self.url}")
                raise Exception("Content-Length missing from headers")
        
        except Exception as e:
            # Diagnostic: Now you will know if the file size failed due to
            # a network timeout or an unexpected response
            logging.error(f"Beim Ermitteln der Datei-Größe ist ein Fehler aufgetreten für {self.url}: {str(e)}")
            logging.error(f"Error determining file size for {self.url}: {str(e)}")
            logging.error("Die Gesamtdateigröße wird auf 0,0 zurückgesetzt.")
            logging.error("Defaulting total_file_size to 0.0")
            self.total_file_size = 0.0
            # NOTE: with total_file_size == 0.0, get_percent() permanently returns
            # -1, so the UI (gui_download.py) falls back to an indeterminate,
            # continuously-pulsing progress bar for the entire download, with no
            # ETA and no percentage ever shown - easy to mistake for a hung/looping
            # download even though bytes may still be arriving underneath. Flag
            # this loudly so it is unambiguous in the log which code path is active.
            logging.warning(
                "total_file_size could not be determined - the download UI will show "
                "an indeterminate/pulsing progress bar instead of a percentage for this file."
            )

    def get_percent(self) -> float:
        return -1 if self.total_file_size == 0.0 else (self.downloaded_file_size / self.total_file_size * 100)

    def get_speed(self) -> float:
        elapsed = time.time() - self.start_time
        return self.downloaded_file_size / elapsed if elapsed > 0 else 0

    def get_time_remaining(self) -> float:
        if self.total_file_size == 0.0: return -1
        speed = self.get_speed()
        return -1 if speed <= 0 else (self.total_file_size - self.downloaded_file_size) / speed

    def get_file_size(self) -> float: return self.total_file_size
    def is_active(self) -> bool: return self.status == DownloadStatus.DOWNLOADING

    # --- STABILIZED CORE ---
    def stop(self) -> None:
        """Non-blocking signal. No longer waits for thread."""
        self.should_stop = True

    def download(self, display_progress: bool = False, spawn_thread: bool = True) -> None:
        """Call this from your UI. If spawn_thread is False, it runs synchronously."""
        # Set status synchronously before the worker thread is scheduled, so callers
        # polling is_active() right after this call never see the pre-thread INACTIVE
        # state and mistake "not started yet" for "failed".
        self.status = DownloadStatus.DOWNLOADING
        if spawn_thread:
            import threading
            threading.Thread(target=self._download, args=(display_progress,), daemon=True).start()
        else:
            self._download(display_progress)

    def _download(self, display_progress: bool = False) -> None:
        """
        Download with full diagnostic tracing.
        """
        utilities.disable_sleep_while_running()
        self.status = DownloadStatus.DOWNLOADING
        logging.info(f"Herunterladen wird gestartet: URL={self.url}, Target={self.filepath}")
        logging.info(f"Initiating download: URL={self.url}, Target={self.filepath}")

        try:
            # Stage 1: Network Check
            if not self.has_network:
                raise ConnectionError("No network connection detected before download.")

            # Stage 2: Filesystem Check
            if not self._prepare_working_directory(self.filepath):
                raise IOError(f"Could not prepare working directory: {self.error_msg}")

            # Stage 3: Request Execution with detailed logging.
            # A stalled socket read is a transient condition, not a fatal
            # one - retry it a bounded number of times, resuming from the
            # last confirmed byte via a Range request, instead of failing
            # (and forcing a full restart of) the entire download.
            attempt = 0
            while True:
                try:
                    self._download_stream()
                    break
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError,
                        requests.exceptions.ChunkedEncodingError) as transient_error:
                    if self.should_stop:
                        raise
                    attempt += 1
                    if attempt > self.MAX_DOWNLOAD_RETRIES:
                        logging.error(f"Maximale Anzahl an Wiederholungsversuchen ({self.MAX_DOWNLOAD_RETRIES}) erreicht, gebe auf.")
                        logging.error(f"Exhausted maximum retries ({self.MAX_DOWNLOAD_RETRIES}), giving up.")
                        raise
                    wait = min(self.RETRY_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), 30)
                    logging.warning(
                        f"Netzwerkfehler beim Herunterladen (Versuch {attempt}/{self.MAX_DOWNLOAD_RETRIES}), "
                        f"setze in {wait}s bei Byte {int(self.downloaded_file_size)} fort: {transient_error}"
                    )
                    logging.warning(
                        f"Transient network error while downloading (attempt {attempt}/{self.MAX_DOWNLOAD_RETRIES}), "
                        f"resuming in {wait}s from byte {int(self.downloaded_file_size)}: {transient_error}"
                    )
                    time.sleep(wait)

            # The retry/resume refactor above updates the running hash incrementally
            # in _download_stream(), but never finalized it into self.checksum - so
            # this stayed None forever and every _validate_installer() comparison
            # against the expected checksum failed unconditionally, even for a
            # perfectly good download. Finalize it here, once, after a full success.
            if self._checksum_storage:
                self.checksum = self._checksum_storage.hexdigest()

            self.download_complete = True
            self.status = DownloadStatus.COMPLETE
            logging.info(f"Herunterladen vollständig abgeschlossen: {self.filename}")
            logging.info(f"Successfully finished download: {self.filename}")

        except Exception as e:
            self.error = True
            self.error_msg = str(e)
            self.status = DownloadStatus.ERROR
            
            # CRITICAL: This will log the entire stack trace (file, line number, and function)
            # You will no longer have to guess where the crash occurred.
            logging.info("FATALES FEHLER WÄHREND HERUNTERLADEN:")
            logging.exception(f"FATAL DOWNLOAD ERROR: {self.url} | Error: {self.error_msg}")
            
        finally:
            # NOTE: status is intentionally NOT overwritten here anymore - it was
            # unconditionally reset to COMPLETE even after the except block above
            # had just set it to ERROR, silently erasing the failure for any caller
            # that inspects .status instead of .error/.download_complete.
            utilities.enable_sleep_after_running()
            logging.info("Netzwerkressourcen freigegeben und Energiespareinstellungen wiederhergestellt.")
            logging.info("Network resources released and sleep settings restored.")

    def _download_stream(self) -> None:
        """
        Opens the network stream and writes chunks to disk.

        If bytes have already been written from a previous attempt, resumes
        via a Range header instead of restarting the file from scratch.
        Raises the underlying exception on any network problem or user
        abort - the caller in _download() decides whether that's worth
        retrying.
        """
        logging.info("Netzwerkstream wird geöffnet...")
        logging.info("Opening network stream...")
        logging.debug("Opening network stream...")

        headers = {}
        mode = 'wb'
        if self.downloaded_file_size > 0:
            headers['Range'] = f"bytes={int(self.downloaded_file_size)}-"
            mode = 'ab'

        # Talk to the session directly here (rather than through
        # NetworkUtilities.get, which swallows connection/timeout errors
        # into an empty Response) so transient failures surface as real
        # exceptions that the retry loop in _download() can catch.
        response = SESSION.get(self.url, stream=True, timeout=15, headers=headers)

        # 200 = full response, 206 = partial/resumed response.
        if response.status_code not in (200, 206):
            raise requests.exceptions.HTTPError(f"HTTP Status Code {response.status_code}")

        if response.status_code == 200 and mode == 'ab':
            # Server ignored our Range request and is resending the whole
            # file - restart the file on disk so it isn't corrupted.
            logging.warning("Server hat Range-Anfrage ignoriert, Download wird neu gestartet.")
            logging.warning("Server ignored Range request, restarting download from scratch.")
            mode = 'wb'
            self.downloaded_file_size = 0.0
            if self._checksum_storage:
                self._checksum_storage = self._checksum_storage.__class__()

        with open(self.filepath, mode) as file:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if self.should_stop:
                    logging.warning(f"Herunterladen gestoppt von Benutzer auf {self.downloaded_file_size} bytes.")
                    logging.warning(f"Download stopped by user at {self.downloaded_file_size} bytes.")
                    raise InterruptedError("Download manually aborted.")

                if chunk:
                    file.write(chunk)
                    self.downloaded_file_size += len(chunk)
                    if self._checksum_storage:
                        self._checksum_storage.update(chunk)

    def _prepare_working_directory(self, path: Path) -> bool:
        try:
            if path.exists(): path.unlink()
            path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except Exception as e:
            self.error_msg = str(e)
            return False
