#!/usr/bin/env python3
"""Find copies of this repo that live outside its fork network under a
typosquatted name, and open issues about them in both repositories."""

import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API = "https://api.github.com"
TOKEN = os.environ["GH_TOKEN"]
ORIGINAL = os.environ["ORIGINAL_REPO"]
MAX_DIST = int(os.environ.get("MAX_DISTANCE", "2"))
FP_COUNT = int(os.environ.get("FINGERPRINT_COMMITS", "20"))
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
CONTACT_SUSPICIOUS = os.environ.get("CONTACT_SUSPICIOUS", "true").lower() == "true"
# Without a PAT the built-in GITHUB_TOKEN is used: it can scan and open issues
# here, but it can't open issues in other people's repositories.
HAS_PAT = os.environ.get("HAS_PAT", "false").lower() == "true"
ALLOWLIST = {s.lower() for s in os.environ.get("ALLOWLIST", "").split()}
# Other names the project goes by (e.g. OCLP-T2); copies named like these count as squats too
ALIASES = os.environ.get("ALIASES", "").split()
LABEL = "typosquat"
OUR_MARKER = "<!-- typosquat:{} -->"
THEIR_MARKER = f"<!-- typosquat-notice-from:{ORIGINAL} -->"

HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "typosquat-watch",
}

# --------------------------------------------------------------------- API --

def request(method, url, body=None):
    if not url.startswith("http"):
        url = API + url
    data = json.dumps(body).encode() if body is not None else None
    for _ in range(6):
        req = urllib.request.Request(url, data=data, method=method, headers=HEADERS)
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None), r.headers
        except urllib.error.HTTPError as e:
            retry_after = e.headers.get("retry-after")
            exhausted = e.headers.get("x-ratelimit-remaining") == "0"
            if e.code in (403, 429) and (retry_after or exhausted):
                if retry_after:
                    wait = int(retry_after)
                else:
                    reset = int(e.headers.get("x-ratelimit-reset", time.time() + 60))
                    wait = reset - int(time.time()) + 2
                wait = max(5, min(wait, 300))
                print(f"  rate limited, sleeping {wait}s")
                time.sleep(wait)
                continue
            return e.code, None, e.headers
    raise RuntimeError(f"giving up on {method} {url}")


def get(path):
    status, data, _ = request("GET", path)
    return data if status < 300 else None


def paginate(path):
    url, out = API + path, []
    while url:
        status, data, headers = request("GET", url)
        if status >= 300 or data is None:
            break
        out.extend(data["items"] if isinstance(data, dict) and "items" in data else data)
        m = re.search(r'<([^>]+)>;\s*rel="next"', headers.get("Link") or "")
        url = m.group(1) if m else None
    return out


def post(path, body):
    status, data, _ = request("POST", path, body)
    return status, data


# ------------------------------------------------------------ name logic --

HOMOGLYPHS = str.maketrans({"0": "o", "1": "l", "i": "l", "3": "e", "4": "a",
                            "5": "s", "7": "t", "8": "b", "9": "g"})

# Non-Latin letters that look like Latin ones (Cyrillic, Greek, a few IPA letters).
# NFKC already folds full-width and "mathematical" letters, so those aren't listed.
CONFUSABLES = str.maketrans({
    # Cyrillic
    "а": "a", "е": "e", "ё": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "ѕ": "s", "і": "i", "ї": "i", "ј": "j", "һ": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w",
    "ӏ": "l", "к": "k",
    "А": "A", "В": "B", "Е": "E", "Ё": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X", "Ѕ": "S", "І": "I", "Ј": "J",
    "Ԛ": "Q", "Ԝ": "W", "Һ": "H", "Ӏ": "l",
    # Greek
    "α": "a", "ε": "e", "ι": "i", "κ": "k", "ν": "v", "ο": "o", "ρ": "p", "υ": "u",
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K", "Μ": "M",
    "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # Latin lookalikes
    "ı": "i", "ɑ": "a", "ɡ": "g", "ℓ": "l",
    # Invisible characters
    "\u200b": None, "\u200c": None, "\u200d": None, "\u2060": None, "\ufeff": None, "\u00ad": None,
})


def fold(text):
    """Map Unicode lookalikes (e.g. Cyrillic 'О') to plain Latin, lowercased."""
    return unicodedata.normalize("NFKC", text or "").translate(CONFUSABLES).lower()


def lev(a, b):
    a, b = a.lower(), b.lower()
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def skeleton(s):
    s = fold(s).replace("rn", "m").replace("vv", "w")
    return re.sub(r"[-_.]", "", s).translate(HOMOGLYPHS)


def looks_like(candidate, original):
    """True if candidate is a near-miss of original (but not identical)."""
    if candidate.lower() == original.lower():
        return False
    if fold(candidate) == fold(original):
        return True  # same name spelled with lookalike Unicode letters
    return skeleton(candidate) == skeleton(original) or lev(fold(candidate), fold(original)) <= MAX_DIST


def variants(name):
    n = name.lower()
    out = {n, n.replace("-", ""), n.replace("-", "_"), n.replace("_", "-")}
    for i in range(len(n)):
        out.add(n[:i] + n[i + 1:])                                # omission
        out.add(n[:i] + n[i] + n[i:])                             # duplication
        if i < len(n) - 1:
            out.add(n[:i] + n[i + 1] + n[i] + n[i + 2:])          # swap
    swaps = {"o": "0", "l": "1", "i": "1", "e": "3", "s": "5", "a": "4", "m": "rn"}
    for i, c in enumerate(n):
        if c in swaps:
            out.add(n[:i] + swaps[c] + n[i + 1:])                 # homoglyph
    return sorted(v for v in out if len(v) >= 3)


# ---------------------------------------------------------- copy evidence --

def fingerprints(repo):
    """Commits that prove a repo was copied from *this* project."""
    branch = repo["default_branch"]
    parent = repo.get("parent")
    if repo.get("fork") and parent:
        cmp = get(f"/repos/{parent['full_name']}/compare/"
                  f"{parent['default_branch']}...{repo['owner']['login']}:{branch}")
        shas = [c["sha"] for c in (cmp or {}).get("commits", [])]
        if shas:
            return list(reversed(shas[-FP_COUNT:]))  # newest first
        print("::warning::This fork has no commits of its own; copies of upstream will also match.")
    commits = get(f"/repos/{ORIGINAL}/commits?sha={branch}&per_page={FP_COUNT}") or []
    return [c["sha"] for c in commits]


def copied_commit(full, shas):
    for sha in shas:
        if get(f"/repos/{full}/commits/{sha}"):
            return sha
    return None


def shared_history(full, limit=100):
    """Where a candidate's history meets this project's.

    Walks the candidate's newest commits and returns (sha, own) for the newest one
    this repo also has, where `own` is how many commits the candidate added on top
    (0 = an unchanged copy). Returns (None, None) if no shared commit is found within
    `limit` commits. Unlike copied_commit(), this also finds copies made long ago.
    """
    commits = get(f"/repos/{full}/commits?per_page={limit}") or []
    for own, c in enumerate(commits):
        if get(f"/repos/{ORIGINAL}/commits/{c['sha']}"):
            return c["sha"], own
    return None, None


def describe_copy(sha, own):
    if not sha:
        return "no"
    if own == 0:
        return f"yes, an unchanged copy (up to `{sha[:12]}`)"
    return f"yes, copied at `{sha[:12]}` with {own} commit{'s' if own != 1 else ''} of its own on top"


# ------------------------------------------------------------ assessment --

def assess(cand, owner_squat, our_owner):
    score, reasons = 0, []
    login = cand["owner"]["login"]
    if owner_squat:
        score += 2
        reasons.append(f"owner name `{login}` imitates `{our_owner}`")
    user = get(f"/users/{login}") or {}
    if user.get("created_at"):
        created = datetime.fromisoformat(user["created_at"].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - created).days
        if age < 90:
            score += 1
            reasons.append(f"owner account is only {age} days old")
    if not cand.get("has_issues"):
        score += 1
        reasons.append("issues are disabled")
    return score >= 2, reasons


# ----------------------------------------------------------------- texts --

def rename_request(full, sha):
    return f"""Hi! 👋

This repository contains the history of [{ORIGINAL}](https://github.com/{ORIGINAL}) (for example commit `{sha[:12]}`), but it is not a GitHub fork, and its name is very close to the original. Users can easily confuse the two, and to them it can look like typosquatting.

We're assuming this wasn't intentional. Could you help avoid the confusion by doing one of the following?

- **Rename the repository** (Settings → General → Repository name) to something clearly distinct, or
- **re-create it as a regular fork**, so GitHub shows "forked from {ORIGINAL}".

A short note in the README saying where the code comes from would also help, and keeps the license attribution intact.

Thanks for understanding!

{THEIR_MARKER}
"""


def suspicious_notice(full, sha, reasons):
    bullet = "\n".join(f"- {r}" for r in reasons)
    return f"""**Notice for users of this repository**

This repository is an unofficial copy of [{ORIGINAL}](https://github.com/{ORIGINAL}) (it contains commit `{sha[:12]}`). It sits outside that project's fork network and uses a confusingly similar name.

Automated checks flagged the following:
{bullet}

The official project lives at https://github.com/{ORIGINAL}. Please download source code and releases only from there.

To the owner: if this was not intentional, please rename the repository or re-create it as a regular fork, and reply here.

{THEIR_MARKER}
"""


def tracking_issue(full, sha, suspicious, reasons, their_issue, note, own=None):
    bullet = "\n".join(f"- {r}" for r in reasons) or "- none"
    action = ("Report it using **Report repository** in the copy's sidebar, so GitHub can review the impersonation."
              if suspicious else
              "Wait for the owner to respond to the rename request." if their_issue else
              "Ask the owner to rename the repository (the workflow couldn't contact them).")
    link = their_issue or note
    return f"""Detected **[{full}](https://github.com/{full})**, which sits outside the fork network and has a typosquatted name.

**Copy of this project:** {describe_copy(sha, own)}

**Assessment:** {"⚠️ suspicious" if suspicious else "likely unintentional"}

**Signals:**
{bullet}

**Issue on their repo:** {link}

**Next step:** {action}

Closing this issue marks the case as handled, and the workflow won't file it again.

{OUR_MARKER.format(full)}
"""


# ------------------------------------------------------------------ main --

def summary(line):
    print(line)
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a") as f:
            f.write(line + "\n")


def main():
    me = (get("/user") or {}).get("login") if HAS_PAT else None
    if HAS_PAT and not me:
        raise SystemExit("TYPOSQUAT_PAT is set but invalid.")
    if not HAS_PAT:
        print("::notice::No TYPOSQUAT_PAT configured; copies will be tracked here but their owners won't be contacted.")
    repo = get(f"/repos/{ORIGINAL}")
    owner, name = repo["owner"]["login"], repo["name"]
    root = (repo.get("source") or repo)["full_name"].lower()
    shas = fingerprints(repo)

    summary(f"## Typosquat scan for `{ORIGINAL}`" + (" (dry run)" if DRY_RUN else ""))

    # 1. Collect name candidates
    names = [name] + ALIASES
    aliases = {a.lower() for a in ALIASES}
    candidates = {}
    for v in sorted({v for n in names for v in variants(n)}):
        q = urllib.parse.quote(f"{v} in:name")
        _, data, _ = request("GET", f"/search/repositories?q={q}&per_page=100")
        time.sleep(2.2)  # search API allows 30 requests per minute
        for item in (data or {}).get("items", []):
            full = item["full_name"]
            c_owner, c_name = item["owner"]["login"], item["name"]
            if c_owner.lower() == owner.lower() or full.lower() in ALLOWLIST:
                continue
            name_sq = c_name.lower() in aliases or any(looks_like(c_name, n) for n in names)
            owner_sq = looks_like(c_owner, owner)
            if name_sq or (owner_sq and c_name.lower() in {n.lower() for n in names}):
                candidates[full] = owner_sq
    print(f"{len(candidates)} name candidates")

    # 2. Skip what's already tracked
    tracked = set()
    if not DRY_RUN:
        post(f"/repos/{ORIGINAL}/labels",
             {"name": LABEL, "color": "d93f0b", "description": "Typosquatting copy of this repo"})
    for issue in paginate(f"/repos/{ORIGINAL}/issues?labels={LABEL}&state=all&per_page=100"):
        m = re.search(r"<!-- typosquat:(\S+) -->", issue.get("body") or "")
        if m:
            tracked.add(m.group(1).lower())

    found = 0
    for full, owner_sq in sorted(candidates.items()):
        if full.lower() in tracked:
            continue
        cand = get(f"/repos/{full}")
        if not cand or cand["full_name"].lower() == root:
            continue
        if cand.get("fork") and (cand.get("source") or {}).get("full_name", "").lower() == root:
            continue  # inside our fork network, so it's a legitimate fork
        sha, own = shared_history(full)
        if not sha:
            sha, own = copied_commit(full, shas), None
        if not sha:
            continue  # similar name, but not a copy of this project

        found += 1
        suspicious, reasons = assess(cand, owner_sq, owner)
        verdict = "suspicious" if suspicious else "likely unintentional"
        summary(f"- **{full}** — {verdict}; {describe_copy(sha, own)}; "
                + ("; ".join(reasons) or "no extra signals"))
        if DRY_RUN:
            continue

        # 3. Issue on their repo
        their_url, note = None, ""
        contact = CONTACT_SUSPICIOUS or not suspicious
        if not contact:
            note = "not contacted (CONTACT_SUSPICIOUS=false)"
        elif not me:
            note = "not contacted, because no TYPOSQUAT_PAT secret is configured"
        elif not cand.get("has_issues"):
            note = "not possible, because issues are disabled"
        else:
            existing = paginate(f"/repos/{full}/issues?creator={me}&state=all&per_page=100")
            prior = next((i for i in existing if THEIR_MARKER in (i.get("body") or "")), None)
            if prior:
                their_url = prior["html_url"]
            else:
                title = (f"Notice: this repository is an unofficial copy of {ORIGINAL}" if suspicious
                         else f"Please consider renaming: name is very close to {ORIGINAL}")
                body = suspicious_notice(full, sha, reasons) if suspicious else rename_request(full, sha)
                status, data = post(f"/repos/{full}/issues", {"title": title, "body": body})
                if status < 300:
                    their_url = data["html_url"]
                else:
                    note = f"failed with HTTP {status} (issues may be restricted, or you may be blocked)"
                time.sleep(3)

        # 4. Tracking issue in our repo
        status, data = post(f"/repos/{ORIGINAL}/issues", {
            "title": f"Typosquatting copy: {full} ({verdict})",
            "body": tracking_issue(full, sha, suspicious, reasons, their_url, note, own),
            "labels": [LABEL],
        })
        if status >= 300:
            print(f"::error::Could not open tracking issue for {full} (HTTP {status})")
        time.sleep(3)

    summary(f"\n{found} new typosquatting copies found.")


if __name__ == "__main__":
    main()
