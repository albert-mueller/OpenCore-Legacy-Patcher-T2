"""
endpoint_health.py: Turn update checks back on when an external service the patcher depends on is gone

The patcher relies on a handful of services that are NOT part of the main repository
(oclp-t2/OpenCore-Legacy-Patcher-T2): the PatcherSupportPkg release downloads, the
Metallib and KDK catalogs, AppleDB, the project website and so on. When one of them
disappears for good, older builds keep pointing at it - root patching or installer
downloads break, and a dead domain or account can be registered again by somebody else.
The fix for that always ships as a patcher update, which a user who snoozed updates
("Snooze Updates") or turned off automatic installation ("Turn Off Auto Updates")
would otherwise not get.

check_and_restore_updates() is called at the start of every update check
(updates.CheckBinaryUpdates.check_binary_updates(), GUI and auto patcher). Only when
updates are snoozed or automatic installation is off, it checks every endpoint group:

  - DOWN    - the server answered 404 / 410 / 451, the domain no longer resolves, or its
              TLS certificate is invalid (a classic sign of a re-registered domain)
  - UP      - any other HTTP answer below 500 (incl. redirects, 401/403/405/429)
  - UNKNOWN - timeouts, 5xx, other connection errors: never counted, a hiccup must not
              change the user's settings

A group is only DOWN when every URL in it is DOWN (eg. the Metallib catalog has several
mirrors - one dead mirror does not break anything). To make sure the user is not simply
offline, github.com itself has to answer first; without that nothing is evaluated.

A group has to stay DOWN for OUTAGE_GRACE_PERIOD (first seen -> seen again) before
anything happens, so a short outage or DNS hiccup never overrides a user's choice. Then
the snooze is lifted and automatic updates are turned back on, both stored in the global
settings, and a notice is saved that the GUI shows once (see pop_notice()).

Each outage is handled once: if the user snoozes or turns automatic updates off again
after seeing the notice, that choice is respected until the endpoint has recovered and
goes down again. The main repository itself is excluded on purpose - if it is gone no
update can be delivered anyway, project_status.py handles that case.
"""

import json
import socket
import logging
import threading

from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

from . import global_settings
from .. import constants


OUTAGE_GRACE_PERIOD = timedelta(hours=24)
REQUEST_TIMEOUT = 8

_GONE_STATUS_CODES = (404, 410, 451)

# Settings keys
_KEY_OUTAGES = "EndpointOutages"         # JSON: {group: {"since": iso, "handled": bool}}
_KEY_NOTICE  = "EndpointRecoveryNotice"  # str, shown once by the GUI

_lock = threading.Lock()


class _State:
    UP      = "up"
    DOWN    = "down"
    UNKNOWN = "unknown"


def _endpoint_groups(global_constants: constants.Constants) -> dict:
    """
    External services the patcher needs at runtime, grouped by what they provide.
    The main repository (and its issues/discussions/releases) is deliberately not
    part of this list.
    """
    from . import kdk_handler, metallib_handler
    from ..sucatalog import products_appledb

    groups = {
        "PatcherSupportPkg downloads": [
            f"{global_constants.url_patcher_support_pkg.rstrip('/')}/{global_constants.patcher_support_pkg_version}/Universal-Binaries.dmg",
        ],
        "Metallib catalog":     list(metallib_handler.METALLIB_API_LINKS),
        "KDK catalog":          [kdk_handler.KDK_API_LINK],
        "AppleDB installer catalog": [products_appledb.APPLEDB_API_URL],
        "Project website":      [global_constants.guide_link],
        "Gemini help":          [global_constants.gemini_link],
    }
    return {name: [url for url in urls if url] for name, urls in groups.items() if any(urls)}


def _is_dns_failure(error: Exception) -> bool:
    current = error
    for _ in range(8):
        if current is None:
            break
        if isinstance(current, socket.gaierror):
            return True
        text = str(current)
        if "NameResolutionError" in text or "Name or service not known" in text \
                or "nodename nor servname" in text or "getaddrinfo failed" in text:
            return True
        current = current.__cause__ or current.__context__ or getattr(current, "reason", None)
    return False


def _probe(url: str) -> str:
    try:
        response = requests.head(url, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if response.status_code == 405:
            response = requests.get(url, timeout=REQUEST_TIMEOUT, allow_redirects=True, stream=True)
            response.close()
    except requests.exceptions.SSLError:
        return _State.DOWN
    except requests.exceptions.ConnectionError as error:
        return _State.DOWN if _is_dns_failure(error) else _State.UNKNOWN
    except Exception:
        return _State.UNKNOWN

    if response.status_code in _GONE_STATUS_CODES:
        return _State.DOWN
    if response.status_code < 500:
        return _State.UP
    return _State.UNKNOWN


def _group_state(urls: list) -> str:
    states = [_probe(url) for url in urls]
    if _State.UP in states:
        return _State.UP
    if states and all(state == _State.DOWN for state in states):
        return _State.DOWN
    return _State.UNKNOWN


def _network_is_up() -> bool:
    try:
        requests.head("https://github.com", timeout=REQUEST_TIMEOUT, allow_redirects=True)
        return True
    except Exception:
        return False


def _updates_restricted(global_constants: constants.Constants, settings: global_settings.GlobalEnviromentSettings) -> bool:
    snoozed = settings.read_property("NextUpdateCheck") not in [None, "", "None"]
    return snoozed or global_constants.auto_update is False


def _load_outages(settings: global_settings.GlobalEnviromentSettings) -> dict:
    raw = settings.read_property(_KEY_OUTAGES)
    if not raw:
        return {}
    try:
        data = json.loads(str(raw))
        return data if isinstance(data, dict) else {}
    except Exception:
        logging.error("EndpointOutages value is invalid and will be reset")
        return {}


def _restore_updates(global_constants: constants.Constants, settings: global_settings.GlobalEnviromentSettings, groups: list) -> None:
    logging.warning(f"External services unreachable for over {int(OUTAGE_GRACE_PERIOD.total_seconds() // 3600)} hours: {', '.join(groups)}")
    logging.warning("Lifting the update snooze and turning automatic updates back on, so the fix can be installed")

    settings.delete_property("NextUpdateCheck")
    settings.write_property("SnoozeUpdates", 0)
    global_constants.snooze_updates = 0

    global_constants.auto_update = True
    settings.write_property("AllowAutoUpdates", True)

    settings.write_property(_KEY_NOTICE, (
        "Some online services OpenCore Legacy Patcher T2 depends on are no longer reachable:\n\n"
        + "\n".join(f"- {group}" for group in groups)
        + "\n\nOlder builds may stop working correctly because of this, and an abandoned domain or "
        "account can be taken over by somebody else. To keep your Mac safe, the update snooze "
        "was lifted and automatic updates were turned back on, so a fixed version is installed "
        "as soon as it is released.\n\n"
        "You can change this again in Settings."
    ))


def check_and_restore_updates(global_constants: constants.Constants) -> bool:
    """
    Returns True if updates were turned back on during this call.
    Never raises - the update check itself must not depend on this.
    """
    try:
        with _lock:
            settings = global_settings.GlobalEnviromentSettings()
            outages = _load_outages(settings)

            if not _updates_restricted(global_constants, settings):
                # Nothing to restore. Forget handled outages of recovered services
                # lazily on the next restricted check instead of probing now.
                return False

            if not _network_is_up():
                logging.info("Endpoint health: offline, skipping")
                return False

            groups = _endpoint_groups(global_constants)
            with ThreadPoolExecutor(max_workers=len(groups) or 1) as pool:
                states = dict(zip(groups, pool.map(_group_state, groups.values())))

            now = datetime.now()
            expired = []
            for group, state in states.items():
                if state == _State.UP:
                    if group in outages:
                        logging.info(f"Endpoint health: {group} is reachable again")
                        outages.pop(group)
                    continue
                if state != _State.DOWN:
                    continue

                entry = outages.setdefault(group, {"since": now.isoformat(timespec="seconds"), "handled": False})
                try:
                    since = datetime.fromisoformat(entry.get("since"))
                except Exception:
                    since = now
                    entry["since"] = now.isoformat(timespec="seconds")
                logging.warning(f"Endpoint health: {group} looks gone (since {entry['since']})")
                if entry.get("handled") is not True and now - since >= OUTAGE_GRACE_PERIOD:
                    expired.append(group)
                    entry["handled"] = True

            # Groups that disappeared from the list (eg. after an update) are dropped
            outages = {group: entry for group, entry in outages.items() if group in states}
            settings.write_property(_KEY_OUTAGES, json.dumps(outages))

            if expired:
                _restore_updates(global_constants, settings, expired)
                return True
            return False
    except Exception:
        logging.exception("Endpoint health check failed, ignoring")
        return False


def pop_notice() -> str:
    """
    Notice for the user after updates were turned back on, or "" if there is none.
    Removed when read, so it is shown once.
    """
    try:
        settings = global_settings.GlobalEnviromentSettings()
        notice = settings.read_property(_KEY_NOTICE)
        if notice:
            settings.delete_property(_KEY_NOTICE)
            return str(notice)
    except Exception:
        logging.exception("Failed to read the endpoint recovery notice")
    return ""
