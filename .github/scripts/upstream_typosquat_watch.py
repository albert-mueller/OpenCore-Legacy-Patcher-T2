#!/usr/bin/env python3
"""Find websites and GitHub repositories that imitate an upstream project
(Dortania's OpenCore Legacy Patcher) and keep ONE public warning issue in this
repository up to date, so users know which ones to avoid.

Nothing is posted anywhere except this repository: neither the upstream
maintainers nor the owners of the listed sites or repositories are contacted.
Closing the warning issue pauses the workflow; reopening it resumes it.
"""

import base64
import concurrent.futures as cf
import datetime
import html
import json
import os
import re
import sys
import time
import urllib.parse

UPSTREAM = os.environ["UPSTREAM_REPO"]
ISSUE_REPO = os.environ["ISSUE_REPO"]
# The shared helpers compare against ORIGINAL_REPO, which here is the upstream project.
os.environ["ORIGINAL_REPO"] = UPSTREAM

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import typosquat_sites as ts  # noqa: E402
from typosquat_watch import assess, get, looks_like, paginate, post, request, summary, variants  # noqa: E402

DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
REPO_NAMES = os.environ.get("REPO_NAMES", UPSTREAM.split("/")[1]).split()
ALLOW_OWNERS = {s.lower() for s in os.environ.get("ALLOWLIST_OWNERS", "").split()}
ALLOW_REPOS = {s.lower() for s in os.environ.get("ALLOWLIST_REPOS", "").split()}
# How long a newly registered lookalike domain without content keeps being re-checked
WATCH_DAYS = int(os.environ.get("WATCH_DAYS", "60"))
# Entries not seen for this long are dropped from the list entirely
FORGET_DAYS = int(os.environ.get("FORGET_DAYS", "365"))
OFFICIAL_SOURCES = os.environ.get("OFFICIAL_SOURCES", "").strip()
PROJECT = os.environ.get("PROJECT_NAME", "OpenCore Legacy Patcher")
# GitHub users mentioned once, in a comment when the warning issue is first created
MENTIONS = [m if m.startswith("@") else "@" + m for m in os.environ.get("MENTIONS", "").split()]

LABEL = "upstream-typosquat"
MARKER = "<!-- upstream-typosquat-tracker -->"
STATE_RE = re.compile(r"<!-- state:([A-Za-z0-9+/=]*) -->")
TITLE = f"⚠️ Fake and lookalike {PROJECT} websites and repositories"
TODAY = datetime.date.today()
BIN_EXT = re.compile(r"\.(dmg|pkg|zip|app|exe|iso|7z|rar|tgz|tar\.gz)$", re.I)
FILE_HOSTS = ("mediafire.com", "mega.nz", "mega.io", "drive.google.com", "dropbox.com", "bit.ly",
              "tinyurl.com", "t.ly", "cutt.ly", "gofile.io", "pixeldrain.com", "workupload.com",
              "sourceforge.net", "anonfiles.com", "files.fm", "1drv.ms", "is.gd", "rebrand.ly")
# Download links in a README that lead here are fine (Apple and the official project)
README_OK_HOSTS = ("apple.com", "dortania.github.io")
GITHUB_HOSTS = ("github.com", "www.github.com", "raw.githubusercontent.com")
MD_LINK = re.compile(r"\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\((https?://[^)\s]+)\)")


def host_in(host, domains):
    return any(host == d or host.endswith("." + d) for d in domains)


def cell(text, limit=80):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) > limit:
        text = text[:limit - 1] + "…"
    return text.replace("|", "\\|").replace("`", "'") or "—"


# ------------------------------------------------------------- GitHub repos --

def readme_download_links(full):
    """Where the README's download links and buttons point, apart from the repo itself,
    the upstream project and Apple. A link counts if it points at an installer or a file
    host, or is labelled as a download ("Download macOS"), wherever it leads, GitHub included."""
    data = get(f"/repos/{full}/readme") or {}
    try:
        text = base64.b64decode(data.get("content", "")).decode("utf-8", "replace")
    except Exception:
        return []
    urls = set(ts.download_links(text, f"https://github.com/{full}/blob/HEAD/"))
    for label, url in MD_LINK.findall(text):
        if ts.DOWNLOAD_WORDS.search(ts.fold(label)) or BIN_EXT.search(urllib.parse.urlsplit(url).path):
            urls.add(url)
    for url in re.findall(r"""(?:\]\(|href\s*=\s*["'])(https?://[^)\s"'<>]+)""", text, re.I):
        parts = urllib.parse.urlsplit(url)
        if BIN_EXT.search(parts.path) or host_in((parts.hostname or "").lower(), FILE_HOSTS):
            urls.add(url)
    own = {full.lower(), UPSTREAM.lower()}
    found = set()
    for url in urls:
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if not host or host_in(host, README_OK_HOSTS):
            continue
        if host in GITHUB_HOSTS:
            repo = "/".join(parts.path.strip("/").split("/")[:2]).lower()
            if repo in own:
                continue  # the repo's own files (its releases are checked separately) or upstream
        found.add(ts.describe_host(url))
    return sorted(found)


def evaluate_repo(full, owner_sq, root, up_owner):
    repo = get(f"/repos/{full}")
    if not repo:
        return None
    if repo.get("fork") and (repo.get("source") or {}).get("full_name", "").lower() == root:
        return None  # a normal fork inside the upstream fork network
    reasons = []
    assets = set()
    for rel in get(f"/repos/{full}/releases?per_page=10") or []:
        assets.update(a["name"] for a in rel.get("assets", []) if BIN_EXT.search(a["name"]))
    if assets:
        shown = ", ".join(sorted(assets)[:3]) + (" …" if len(assets) > 3 else "")
        reasons.append(f"publishes its own downloads ({shown})")
    ext = readme_download_links(full)
    if ext:
        reasons.append("README links to downloads on " + ", ".join(ext[:3]))
    suspicious, signals = assess(repo, owner_sq, up_owner)
    if not reasons and not suspicious:
        return None  # a harmless copy without its own downloads
    return {"reasons": cell("; ".join(reasons + signals), 160)}


def scan_repos():
    up_owner = UPSTREAM.split("/")[0]
    root = ((get(f"/repos/{UPSTREAM}") or {}).get("full_name") or UPSTREAM).lower()
    exact = {n.lower() for n in REPO_NAMES}
    candidates = {}
    for v in sorted({v for n in REPO_NAMES for v in variants(n)}):
        path = f"/search/repositories?q={urllib.parse.quote(v + ' in:name')}&per_page=100"
        items = paginate(path) if v in exact else ((request("GET", path)[1] or {}).get("items") or [])
        time.sleep(2.2)  # the search API allows 30 requests per minute
        for item in items:
            full, owner, name = item["full_name"], item["owner"]["login"], item["name"]
            if (owner.lower() == up_owner.lower() or owner.lower() in ALLOW_OWNERS
                    or full.lower() in ALLOW_REPOS):
                continue
            if name.lower() in exact or any(looks_like(name, n) for n in REPO_NAMES):
                candidates[full] = looks_like(owner, up_owner)
    print(f"{len(candidates)} repository candidates")
    found = {}
    for full, owner_sq in sorted(candidates.items()):
        info = evaluate_repo(full, owner_sq, root, up_owner)
        if info:
            found[full] = info
    return found


# ----------------------------------------------------------------- websites --

def scan_sites(watch):
    targets = {h: "/" for h in ts.domain_candidates(ts.KEYWORDS)}
    for kw in ts.CT_KEYWORDS:
        for h in ts.ct_hosts(kw):
            targets.setdefault(h, "/")
    if ts.NRD_KEYWORDS:
        for d in ts.nrd_domains(ts.NRD_DAYS):
            if ts.nrd_match(d):
                watch.setdefault(d, TODAY.isoformat())
    cutoff = (TODAY - datetime.timedelta(days=WATCH_DAYS)).isoformat()
    for h, since in list(watch.items()):
        if since < cutoff:
            del watch[h]
        else:
            targets.setdefault(h, "/")
    targets = {h: p for h, p in targets.items() if not ts.allowed(h, "")}
    print(f"{len(targets)} hostnames to check")
    with cf.ThreadPoolExecutor(max_workers=48) as ex:
        results = [r for r in ex.map(lambda hp: ts.inspect(*hp), targets.items()) if r]
    found = {}
    for r in results:
        found[r["host"]] = {
            "title": cell(html.unescape(r["title"])),
            "downloads": cell(", ".join(r["download_hosts"]) or "no download links", 60),
        }
        watch.pop(r["host"], None)  # it has content now, so it's listed normally
    return found


# ------------------------------------------------------------ tracker issue --

def load_state(body):
    m = STATE_RE.search(body or "")
    if not m:
        return {"sites": {}, "repos": {}, "watch": {}}
    state = json.loads(base64.b64decode(m.group(1)))
    for k in ("sites", "repos", "watch"):
        state.setdefault(k, {})
    return state


def merge(store, found):
    new = []
    for key, info in found.items():
        entry = store.setdefault(key, {"first_seen": TODAY.isoformat()})
        if len(entry) == 1:
            new.append(key)
        entry.update(info, last_seen=TODAY.isoformat())
    forget = (TODAY - datetime.timedelta(days=FORGET_DAYS)).isoformat()
    for key in [k for k, e in store.items() if e.get("last_seen", "") < forget]:
        del store[key]
    return new


def render(state):
    today = TODAY.isoformat()
    sites = {h: e for h, e in state["sites"].items() if e["last_seen"] == today}
    repos = {r: e for r, e in state["repos"].items() if e["last_seen"] == today}
    gone_sites = {h: e for h, e in state["sites"].items() if h not in sites}
    gone_repos = {r: e for r, e in state["repos"].items() if r not in repos}
    out = [
        f"The websites and GitHub repositories below use names that imitate **{PROJECT}**. "
        "They are **not** run by its developers or by this project. Please don't download anything "
        "from them: lookalike download pages are a common way to spread malware.",
        "",
        "**Official sources**",
        OFFICIAL_SOURCES or f"- https://github.com/{UPSTREAM}",
        "",
        "This list is updated automatically twice a day. **Subscribe** to this issue to be notified when "
        "new entries are found. If an entry is wrong, or you've found one that's missing, leave a comment.",
        "",
        f"### Websites ({len(sites)})",
    ]
    if sites:
        out += ["| Site | Page title | Download links point to | First seen |", "|---|---|---|---|"]
        out += [f"| `{h}` | {e['title']} | {e['downloads']} | {e['first_seen']} |" for h, e in sorted(sites.items())]
    else:
        out.append("None found right now.")
    out += ["", f"### GitHub repositories ({len(repos)})"]
    if repos:
        out += ["| Repository | Why it's listed | First seen |", "|---|---|---|"]
        out += [f"| `{r}` | {e['reasons']} | {e['first_seen']} |" for r, e in sorted(repos.items())]
    else:
        out.append("None found right now.")
    if gone_sites or gone_repos:
        out += ["", f"<details><summary>No longer reachable ({len(gone_sites) + len(gone_repos)})</summary>", ""]
        out += [f"- `{k}` (last seen {e['last_seen']})"
                for k, e in sorted({**gone_sites, **gone_repos}.items())]
        out += ["", "</details>"]
    out += [
        "",
        "<sub>Found with automated checks: lookalike domain names, certificate-transparency logs, "
        "newly registered domains and GitHub search. Repositories are only listed if they aren't forks "
        "and offer their own downloads or show other impersonation signals. A listing means the name "
        "imitates the project; it isn't proof that the content is malicious. Names are shown as code on "
        "purpose, so they aren't clickable.</sub>",
        "",
        MARKER,
        f"<!-- state:{base64.b64encode(json.dumps(state, separators=(',', ':')).encode()).decode()} -->",
    ]
    return "\n".join(out)


def find_tracker():
    path = f"/repos/{ISSUE_REPO}/issues?labels={LABEL}&state=all&per_page=100"
    status, _, _ = request("GET", path)
    if status >= 300:
        # Don't mistake an API hiccup for "no warning issue yet" and open a duplicate
        raise SystemExit(f"Could not look up the warning issue (HTTP {status}); try again later.")
    for issue in paginate(path):
        if MARKER in (issue.get("body") or "") and "pull_request" not in issue:
            return issue
    return None


def pin(issue):
    q = "mutation($id: ID!) { pinIssue(input: {issueId: $id}) { issue { number } } }"
    status, data, _ = request("POST", "/graphql", {"query": q, "variables": {"id": issue["node_id"]}})
    if status >= 300 or (data or {}).get("errors"):
        print("::notice::Couldn't pin the warning issue; pin it by hand if you want it on top.")


def main():
    summary(f"## Lookalike scan for `{UPSTREAM}`, reported in `{ISSUE_REPO}`" + (" (dry run)" if DRY_RUN else ""))
    tracker = find_tracker()
    if tracker and tracker["state"] == "closed":
        summary(f"The warning issue #{tracker['number']} is closed, so the scan is paused. Reopen it to resume.")
        return
    state = load_state(tracker["body"] if tracker else "")

    new_sites = merge(state["sites"], scan_sites(state["watch"]))
    new_repos = merge(state["repos"], scan_repos())
    body = render(state)
    if len(body) > 65000:
        print("::warning::Warning issue is close to GitHub's size limit; consider lowering FORGET_DAYS.")

    for h in new_sites:
        summary(f"- new site: `{h}` — {state['sites'][h]['title']}")
    for r in new_repos:
        summary(f"- new repository: `{r}` — {state['repos'][r]['reasons']}")
    summary(f"\n{len(new_sites)} new sites, {len(new_repos)} new repositories.")
    if DRY_RUN:
        return

    if not tracker:
        post(f"/repos/{ISSUE_REPO}/labels",
             {"name": LABEL, "color": "b60205", "description": f"Lookalike sites and repos imitating {PROJECT}"})
        status, data = post(f"/repos/{ISSUE_REPO}/issues", {"title": TITLE, "body": body, "labels": [LABEL]})
        if status >= 300:
            raise SystemExit(f"Could not open the warning issue (HTTP {status})")
        pin(data)
        if MENTIONS:
            post(f"/repos/{ISSUE_REPO}/issues/{data['number']}/comments",
                 {"body": "cc " + " ".join(MENTIONS) + 
                  " for awareness: this issue lists lookalike sites and repositories imitating "
                  f"{PROJECT}. It's updated automatically; no action needed."})
        return

    if body != tracker["body"]:
        status, _, _ = request("PATCH", f"/repos/{ISSUE_REPO}/issues/{tracker['number']}", {"body": body})
        if status >= 300:
            raise SystemExit(f"Could not update the warning issue (HTTP {status})")
    if new_sites or new_repos:
        lines = ["**New entries found today:**", ""]
        lines += [f"- website `{h}`" for h in new_sites]
        lines += [f"- repository `{r}`" for r in new_repos]
        lines += ["", "The list above has been updated."]
        post(f"/repos/{ISSUE_REPO}/issues/{tracker['number']}/comments", {"body": "\n".join(lines)})


if __name__ == "__main__":
    main()
