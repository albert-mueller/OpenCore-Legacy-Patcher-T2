#!/usr/bin/env python3
"""Find GitHub repositories and websites that present themselves as this
project without being part of it, whatever their name, and open a tracking
issue in this repository for each one."""

import base64
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from typosquat_watch import (copied_commit, describe_copy, fingerprints, fold, get, paginate, post,
                             request, shared_history, summary)  # noqa: E402
from typosquat_sites import DOWNLOAD_EXT, UA, fetch, rdap  # noqa: E402

ORIGINAL = os.environ["ORIGINAL_REPO"]
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
BRAVE_KEY = os.environ.get("BRAVE_API_KEY", "").strip()
LABEL = "impersonation"
MARKER = "<!-- impersonation:{} -->"
OFFICIAL_HOSTS = ("github.com", "githubusercontent.com")


def _list(var, sep):
    return [x.strip() for x in os.environ.get(var, "").split(sep) if x.strip()]


def norm(text):
    """Fold Unicode lookalikes, lowercase, treat - and _ as spaces, collapse whitespace."""
    return re.sub(r"\s+", " ", re.sub(r"[-_]", " ", fold(text)))


MARKERS = sorted({norm(m) for m in _list("CONTENT_MARKERS", "|")})
REPO_QUERIES = _list("REPO_QUERIES", "|")
WEB_QUERIES = _list("WEB_QUERIES", "|")
ALLOW_REPOS = {r.lower() for r in _list("ALLOWLIST_REPOS", " ")}
ALLOW_HOSTS = {h.lower() for h in _list("ALLOWLIST_HOSTS", " ")}


# --------------------------------------------------------------- helpers --

def mentions(text):
    t = norm(text)
    return [m for m in MARKERS if m in t]


def host_in(host, hosts):
    return any(host == h or host.endswith("." + h) for h in hosts)


def off_github_downloads(base, text):
    """Hosts outside GitHub that download links (by file type or link text) point to."""
    links = []
    for label, url in re.findall(r"\[([^\]]*)\]\(([^)\s]+)", text or ""):            # Markdown
        links.append((label, url))
    for url, label in re.findall(r"""<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>(.*?)</a>""",
                                 text or "", re.I | re.S):                              # HTML
        links.append((label, url))
    hosts = set()
    for label, url in links:
        if DOWNLOAD_EXT.search(url) or "download" in label.lower():
            host = (urllib.parse.urlsplit(urllib.parse.urljoin(base, url)).hostname or "").lower()
            if host and not host_in(host, OFFICIAL_HOSTS):
                hosts.add(host)
    return sorted(hosts)


def account_age_days(login):
    user = get(f"/users/{login}") or {}
    if not user.get("created_at"):
        return None
    created = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
    return (datetime.now(timezone.utc) - created).days


def readme(full):
    data = get(f"/repos/{full}/readme")
    if not data or data.get("encoding") != "base64":
        return ""
    return base64.b64decode(data["content"]).decode("utf-8", "replace")


# ---------------------------------------------------------- GitHub repos --

def scan_repos(owner, root, shas, tracked):
    found, seen = [], set()
    for q in REPO_QUERIES:
        _, data, _ = request("GET", f"/search/repositories?q={urllib.parse.quote(q)}&per_page=100")
        time.sleep(2.2)  # search API allows 30 requests per minute
        for item in (data or {}).get("items", []):
            full = item["full_name"]
            key = full.lower()
            if key in seen:
                continue
            seen.add(key)
            if (item["owner"]["login"].lower() == owner.lower() or key == root
                    or key in ALLOW_REPOS or key in tracked):
                continue
            cand = get(f"/repos/{full}") or item
            source = (cand.get("source") or {}).get("full_name", "")
            if cand.get("fork") and source.lower() == root:
                continue  # a normal fork of this project
            in_meta = mentions(f"{cand['name']} {cand.get('description') or ''}")
            text = readme(full)
            in_readme = mentions(text)
            downloads = off_github_downloads(f"https://github.com/{full}/", text)
            # Presents itself as the project in its name/description, or its README
            # talks about the project and sends people to downloads outside GitHub.
            if not in_meta and not (in_readme and downloads):
                continue
            sha, own = shared_history(full)
            if not sha:
                sha, own = copied_commit(full, shas), None
            if own == 0 and not downloads:
                # An unchanged copy of this project (a backup or mirror) shows nothing that
                # isn't ours. It's skipped, not filed, so it's checked again on every run
                # and gets flagged as soon as its owner changes anything.
                print(f"  skipped {full}: unchanged copy, no outside downloads")
                continue
            found.append({
                "kind": "repo", "key": key, "name": full, "url": cand["html_url"],
                "description": (cand.get("description") or "").strip(),
                "where": ", ".join(x for x, hit in (("name/description", in_meta), ("README", in_readme)) if hit),
                "copy": describe_copy(sha, own),
                "fork_of": source if cand.get("fork") else None,
                "downloads": downloads,
                "owner_age": account_age_days(cand["owner"]["login"]),
                "pushed": (cand.get("pushed_at") or "")[:10],
            })
    return found


# -------------------------------------------------------------- websites --

def brave_search(query):
    url = "https://api.search.brave.com/res/v1/web/search?" + urllib.parse.urlencode({"q": query, "count": 20})
    req = urllib.request.Request(url, headers={
        "Accept": "application/json", "X-Subscription-Token": BRAVE_KEY, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"::warning::Web search for {query!r} failed: {e}")
        return []
    return [res["url"] for res in data.get("web", {}).get("results", []) if res.get("url")]


def scan_web(site_host, tracked):
    if not BRAVE_KEY:
        print("::notice::No BRAVE_API_KEY secret configured; skipping the website search.")
        return []
    by_host = {}
    for q in WEB_QUERIES:
        for url in brave_search(q):
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
            if host and host != site_host and not host_in(host, OFFICIAL_HOSTS) and not host_in(host, ALLOW_HOSTS):
                by_host.setdefault(host, url)
        time.sleep(1.5)  # stay under the free tier's rate limit

    found = []
    for host, url in sorted(by_host.items()):
        if host in tracked:
            continue
        status, final, html = fetch(url)
        if not html:
            continue
        title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        title = re.sub(r"\s+", " ", title.group(1)).strip()[:150] if title else ""
        in_title = mentions(title)
        in_page = mentions(html)
        downloads = off_github_downloads(final, html)
        # Calls itself the project in its title, or talks about it and offers downloads elsewhere.
        if not in_title and not (in_page and downloads):
            continue
        found.append({
            "kind": "site", "key": host, "name": host, "url": final, "status": status, "title": title,
            "where": "page title" if in_title else "page text",
            "downloads": downloads,
            "links_official": f"github.com/{ORIGINAL}".lower() in html.lower(),
        })
    return found


# ----------------------------------------------------------------- texts --

def repo_body(f):
    age = f"{f['owner_age']} days" if f["owner_age"] is not None else "—"
    lines = [
        f"Found a GitHub repository that presents itself as this project but isn't part of its fork network: "
        f"**[{f['name']}]({f['url']})**",
        "",
        f"- **Description:** {f['description'] or '—'}",
        f"- **Mentions the project in:** {f['where']}",
        f"- **Copy of this project:** {f['copy']}",
        f"- **Fork of:** {f['fork_of'] or 'not a GitHub fork (imported, mirrored or detached)'}",
        f"- **Download links outside GitHub:** {', '.join(f['downloads']) or 'none'}",
        f"- **Owner account age:** {age}",
        f"- **Last pushed:** {f['pushed'] or '—'}",
        "",
        "This may be a legitimate related project, such as an EFI config, a guide or a fan build. "
        "Please check it before acting.",
        "",
        "**If it's an impersonation:** report it using **Report repository** in its sidebar.",
        "**If it's legitimate:** close this issue (it won't be filed again), or add it to "
        "`ALLOWLIST_REPOS` in `impersonation-watch.yml`.",
    ]
    return lines


def site_body(f):
    reg = rdap(f["name"]) if not f["name"].endswith(".github.io") else {}
    lines = [
        f"Found a website that presents itself as this project: **{f['name']}**",
        "",
        f"- **URL:** {f['url']} (HTTP {f['status']})",
        f"- **Page title:** {f['title'] or '—'}",
        f"- **Mentions the project in:** {f['where']}",
        f"- **Links to the official repo:** {'yes' if f['links_official'] else 'no'}",
        f"- **Download links outside GitHub:** {', '.join(f['downloads']) or 'none'}",
    ]
    if reg:
        lines += [
            f"- **Registrar:** {reg.get('registrar') or '—'}",
            f"- **Registrar abuse contact:** {reg.get('abuse') or '—'}",
            f"- **Registered:** {reg.get('registered') or '—'}",
        ]
    lines += [
        "",
        "This may be a legitimate article, review or mirror. Please check it before acting.",
        "",
        "**If it's an impersonation:** report it to the registrar's abuse contact and the hosting provider, "
        "and submit it to [Google Safe Browsing](https://safebrowsing.google.com/safebrowsing/report_phish/).",
        "**If it's legitimate:** close this issue (it won't be filed again), or add the host to "
        "`ALLOWLIST_HOSTS` in `impersonation-watch.yml`.",
    ]
    return lines


# ------------------------------------------------------------------ main --

def main():
    repo = get(f"/repos/{ORIGINAL}")
    owner = repo["owner"]["login"]
    root = (repo.get("source") or repo)["full_name"].lower()
    pages_url = (get(f"/repos/{ORIGINAL}/pages") or {}).get("html_url") or ""
    site_host = (urllib.parse.urlsplit(pages_url).hostname or "").lower()
    shas = fingerprints(repo)

    # Anything the other two watchers already filed counts as tracked, too.
    tracked = set()
    for label, pattern in (("impersonation", r"<!-- impersonation:(\S+) -->"),
                           ("typosquat", r"<!-- typosquat:(\S+) -->"),
                           ("typosquat-site", r"<!-- typosquat-site:(\S+) -->")):
        for issue in paginate(f"/repos/{ORIGINAL}/issues?labels={label}&state=all&per_page=100"):
            m = re.search(pattern, issue.get("body") or "")
            if m:
                tracked.add(m.group(1).lower())

    summary(f"## Impersonation scan for `{ORIGINAL}`" + (" (dry run)" if DRY_RUN else ""))
    findings = scan_repos(owner, root, shas, tracked) + scan_web(site_host, tracked)

    if findings and not DRY_RUN:
        post(f"/repos/{ORIGINAL}/labels",
             {"name": LABEL, "color": "5319e7", "description": "Repo or site presenting itself as this project"})

    for f in findings:
        summary(f"- **{f['name']}** ({f['kind']}) — mentions the project in {f['where']}"
                + (f"; downloads from {', '.join(f['downloads'])}" if f["downloads"] else ""))
        if DRY_RUN:
            continue
        lines = repo_body(f) if f["kind"] == "repo" else site_body(f)
        status, _ = post(f"/repos/{ORIGINAL}/issues", {
            "title": f"Possible impersonation: {f['name']}",
            "body": "\n".join(lines + ["", MARKER.format(f["key"])]),
            "labels": [LABEL],
        })
        if status >= 300:
            print(f"::error::Could not open an issue for {f['name']} (HTTP {status})")
        time.sleep(3)

    summary(f"\n{len(findings)} new possible impersonations found.")


if __name__ == "__main__":
    main()
