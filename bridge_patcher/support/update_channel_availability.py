"""
update_channel_availability.py: Hide fork update channels whose GitHub account/repository is gone

Every channel except "official" points at somebody else's GitHub account. If that
account is deleted, renamed (and the old name not redirected), suspended or the
repository is taken down, the updater would keep hitting a dead URL forever and
the user would be stuck on a channel that can never deliver anything.

refresh() checks every non-official channel once per session:
  - definitely gone (HTTP 404 / 410 / 451)  -> channel is marked offline:
        * it disappears from the "Update Channel" dropdown
        * if it was the selected channel, the patcher falls back to "official"
          and stores that choice, so the next update check uses the main project
  - reachable (HTTP 2xx, incl. GitHub's redirect after a rename) -> online
  - anything else (no internet, timeout, GitHub hiccup, 5xx, 429 ...) -> unknown,
    the channel is left untouched. A user who is simply offline must never be
    moved away from the channel they picked.

The check uses the public github.com page (HEAD) instead of api.github.com:
the unauthenticated API answers 403 once its 60 requests/hour are used up,
which is indistinguishable from other errors, while github.com reliably answers
404 for a deleted account or repository.

The result is NOT persisted - if the account comes back, the channel shows up
again on the next launch (the user stays on "official" until they pick it again).
"""

import logging
import threading

import requests

from . import global_settings
from . import network_handler


OFFICIAL_CHANNEL = "official"

# Status codes that mean "this repository/account does not exist (anymore)"
_GONE_STATUS_CODES = (404, 410, 451)

_lock = threading.Lock()


def _probe(repo_url: str) -> "bool | None":
    """
    True = online, False = definitely gone, None = could not tell
    """
    try:
        response = network_handler.SESSION.head(repo_url.rstrip("/"), timeout=5, allow_redirects=True)
    except requests.exceptions.RequestException as error:
        logging.info(f"Could not check update channel repository {repo_url}: {error}")
        return None

    if response.status_code in _GONE_STATUS_CODES:
        return False
    if 200 <= response.status_code < 300:
        return True
    logging.info(f"Update channel repository {repo_url} answered HTTP {response.status_code}, treating as unknown")
    return None


def is_channel_online(constants, channel_key: str, force: bool = False) -> bool:
    """
    Returns False only if the channel's repository is known to be gone.
    Unknown/unreachable counts as online (see module docstring).
    """
    if channel_key == OFFICIAL_CHANNEL:
        return True
    if channel_key not in constants.update_channels:
        return False

    with _lock:
        if not force and channel_key in constants.update_channel_status:
            return constants.update_channel_status[channel_key] is not False

        result = _probe(constants.update_channels[channel_key]["repo"])
        constants.update_channel_status[channel_key] = result
        if result is False:
            logging.warning(f"Update channel \"{channel_key}\" is offline ({constants.update_channels[channel_key]['repo']} no longer exists)")
            _fall_back_if_selected(constants, channel_key)
        return result is not False


def _fall_back_if_selected(constants, channel_key: str) -> None:
    """
    Switch the selected channel back to the official project if it pointed at
    the channel that just went offline. Caller holds _lock.
    """
    if constants.update_channel != channel_key:
        return

    logging.warning(f"Selected update channel \"{channel_key}\" is offline, switching back to \"{OFFICIAL_CHANNEL}\"")
    constants.update_channel = OFFICIAL_CHANNEL
    # Results of an earlier check came from the dead repository
    constants.has_checked_updates = False
    if global_settings.GlobalEnviromentSettings().write_property("UpdateChannel", OFFICIAL_CHANNEL) is not True:
        # Still on "official" for this session; next launch re-detects and retries
        logging.error(f"Failed to store UpdateChannel={OFFICIAL_CHANNEL!r} after fallback")
    # installed_update_channel is intentionally left alone: if the installed
    # build came from the dead fork, update_channel_switch_pending becomes True
    # and the updater offers the newest official build, even if its version
    # number is lower than the fork build - which is exactly the way back.


def refresh(constants) -> None:
    """
    Check every fork channel. Safe to call from a background thread.
    """
    for channel_key in list(constants.update_channels):
        if channel_key == OFFICIAL_CHANNEL:
            continue
        is_channel_online(constants, channel_key)


def refresh_in_background(constants) -> threading.Thread:
    thread = threading.Thread(target=refresh, args=(constants,), daemon=True, name="update-channel-availability")
    thread.start()
    return thread
