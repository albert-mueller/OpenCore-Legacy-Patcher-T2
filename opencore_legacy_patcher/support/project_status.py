"""
project_status.py: Detect whether the main project (OpenCore-Legacy-Patcher-T2) reached its end of life

The official repository (constants.repo_link) is checked once per session:

  ACTIVE    - repository exists, is not archived and is still the same repository
  ARCHIVED  - repository exists but was archived (read-only, no further development)
  DELETED   - github.com / the API answers 404, 410 or 451 (account or repository gone,
              taken down, or made private)
  REPLACED  - a repository exists under the old name, but its GitHub repository ID differs
              from the original one. That happens when the account or repository was deleted
              and somebody else registered the name again - nothing from it may be trusted.
  None      - could not tell (offline, timeout, rate limit, 5xx ...). Never treated as end of
              life, so a user who is simply offline is never shown the warning.

ARCHIVED, DELETED and REPLACED all count as end of life: the main menu shows a warning and a
button to download the upstream OpenCore Legacy Patcher from Dortania instead.

DELETED and REPLACED additionally stop the in-app updater from using the official channel
(see updates.py): once the original repository is gone, whoever registers the name next would
otherwise be able to ship "updates" to every installation (same supply chain issue as the
fork channels in update_channel_availability.py). ARCHIVED keeps the updater untouched - an
archived repository is read-only, nothing new can be published there.

A renamed repository is ACTIVE: GitHub redirects the old name and the API returns the same ID.
The result is not persisted; if the repository comes back, the warning disappears on the next launch.
"""

import enum
import logging
import threading

import requests

from . import network_handler


# GitHub's ID of albert-mueller/OpenCore-Legacy-Patcher-T2. Survives renames and transfers, but
# a repository created later under the same name always gets a new one.
MAIN_PROJECT_REPOSITORY_ID = 1207616246

DORTANIA_RELEASES_URL = "https://github.com/dortania/OpenCore-Legacy-Patcher/releases/latest"

_GONE_STATUS_CODES = (404, 410, 451)

_lock = threading.Lock()


class ProjectStatus(enum.Enum):
    ACTIVE   = "Active"
    ARCHIVED = "Archived"
    DELETED  = "Deleted"
    REPLACED = "Replaced"


END_OF_LIFE_STATES = (ProjectStatus.ARCHIVED, ProjectStatus.DELETED, ProjectStatus.REPLACED)
UNTRUSTED_STATES   = (ProjectStatus.DELETED, ProjectStatus.REPLACED)


def _api_url(repo_link: str) -> str:
    return repo_link.replace("https://github.com/", "https://api.github.com/repos/").strip("/")


def _probe_api(repo_link: str) -> "ProjectStatus | None":
    """
    Ask the GitHub API. Only this tells "archived" and the repository ID apart.
    The unauthenticated API answers 403/429 once its 60 requests/hour are used up,
    which counts as "could not tell".
    """
    try:
        response = network_handler.SESSION.get(
            _api_url(repo_link),
            timeout=5,
            allow_redirects=True,
            headers={"Accept": "application/vnd.github+json"},
        )
    except requests.exceptions.RequestException as error:
        logging.info(f"Could not check main project status via API: {error}")
        return None

    if response.status_code in _GONE_STATUS_CODES:
        return ProjectStatus.DELETED

    if response.status_code != 200:
        logging.info(f"GitHub API answered HTTP {response.status_code} for the main project, falling back")
        return None

    try:
        data = response.json()
    except ValueError:
        logging.info("GitHub API returned no valid JSON for the main project, falling back")
        return None

    if not isinstance(data, dict):
        return None

    repository_id = data.get("id")
    if isinstance(repository_id, int) and repository_id != MAIN_PROJECT_REPOSITORY_ID:
        logging.warning(
            f"Main project repository ID changed ({MAIN_PROJECT_REPOSITORY_ID} -> {repository_id}): "
            "the original repository was deleted and the name was registered again"
        )
        return ProjectStatus.REPLACED

    if data.get("archived") is True:
        return ProjectStatus.ARCHIVED

    return ProjectStatus.ACTIVE


def _probe_page(repo_link: str) -> "ProjectStatus | None":
    """
    Fallback when the API is rate limited: github.com reliably answers 404 for a deleted
    account or repository. It can't tell "archived" or a re-registered name, so a reachable
    page only means "could not tell", never ACTIVE.
    """
    try:
        response = network_handler.SESSION.head(repo_link.rstrip("/"), timeout=5, allow_redirects=True)
    except requests.exceptions.RequestException as error:
        logging.info(f"Could not check main project page: {error}")
        return None

    if response.status_code in _GONE_STATUS_CODES:
        return ProjectStatus.DELETED
    return None


def check(constants, force: bool = False) -> "ProjectStatus | None":
    """
    Returns the status of the main project, cached per session in constants.project_status.
    """
    with _lock:
        if not force and constants.project_status_checked:
            return constants.project_status

        status = _probe_api(constants.repo_link)
        if status is None:
            status = _probe_page(constants.repo_link)

        constants.project_status = status
        constants.project_status_checked = True

        if status in END_OF_LIFE_STATES:
            logging.warning(f"Main project ({constants.repo_link}) reached end of life: {status.value}")
        else:
            logging.info(f"Main project status: {status.value if status else 'unknown'}")
        return status


def is_end_of_life(constants) -> bool:
    return constants.project_status in END_OF_LIFE_STATES


def is_untrusted(constants) -> bool:
    """
    True if the official repository can no longer be trusted as an update source
    """
    return constants.project_status in UNTRUSTED_STATES
