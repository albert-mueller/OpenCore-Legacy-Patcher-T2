#!/usr/bin/env python3
"""
Find security fixes disclosed in CHANGELOG.md that have no GitHub Security
Advisory (GHSA) yet, and create a repository advisory for each of them.

Environment:
  GITHUB_REPOSITORY   owner/repo (set by Actions)
  ADVISORY_TOKEN      fine-grained PAT with "Repository security advisories: read & write"
  MODELS_TOKEN        token with models:read (GITHUB_TOKEN works) - optional
  GEMINI_API_KEY      free Google AI Studio key, fallback when GitHub Models fails - optional
  CHANGELOG_PATH      default: CHANGELOG.md
  STOP_AT             stop parsing at this version heading (skip older / upstream history)
  DRY_RUN             "true" -> only report, create nothing
  PUBLISH             "true" -> publish advisories instead of leaving them as drafts
  USE_AI              "false" -> keyword matching only
  ADVISORY_ECOSYSTEM  default: other
  ADVISORY_PACKAGE    default: repository name
  AI_DELAY            seconds between AI calls (default 4)
  REJECTED_CACHE      file with entry ids the AI rejected (kept via actions/cache)
"""
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

API = "https://api.github.com"
REPO = os.environ["GITHUB_REPOSITORY"]
TOKEN = os.environ.get("ADVISORY_TOKEN", "")
MODELS_TOKEN = os.environ.get("MODELS_TOKEN", "")
CHANGELOG = os.environ.get("CHANGELOG_PATH", "CHANGELOG.md")
STOP_AT = os.environ.get("STOP_AT", "").strip().lstrip("v")
DRY_RUN = os.environ.get("DRY_RUN", "false").lower() == "true"
PUBLISH = os.environ.get("PUBLISH", "false").lower() == "true"
USE_AI = os.environ.get("USE_AI", "true").lower() == "true" and bool(
    MODELS_TOKEN or os.environ.get("GEMINI_API_KEY"))
ECOSYSTEM = os.environ.get("ADVISORY_ECOSYSTEM", "other")
PACKAGE = os.environ.get("ADVISORY_PACKAGE") or REPO.split("/")[1]
MODEL = os.environ.get("MODEL", "openai/gpt-4.1-mini")
AI_DELAY = float(os.environ.get("AI_DELAY", "4"))
REJECTED_CACHE = os.environ.get("REJECTED_CACHE", ".ghsa-sync-cache/rejected.txt")

MARKER = "changelog-ghsa-id:"
SEVERITIES = {"low", "medium", "high", "critical"}

KEYWORDS = re.compile(
    r"security (?:fix|issue|hole|flaw|bug|vulnerab|risk|problem|patch)|vulnerab|exploit|CVE-\d{4}-\d+|GHSA-|injection|traversal|"
    r"privilege|escalat|arbitrary (?:code|command|file)|remote code|\bRCE\b|"
    r"\bXSS\b|\bCSRF\b|\bSSRF\b|saniti[sz]|insecure|unsafe|hard-?coded "
    r"(?:secret|token|key|password)|credential|token leak|TOCTOU|"
    r"buffer overflow|out-of-bounds|use-after-free|signature (?:check|verif)|"
    r"integrity check|code signing|man-in-the-middle|\bMITM\b|downgrade attack|"
    r"attacker|social engineering|typosquat|phishing|malware",
    re.I,
)
ID_RE = re.compile(r"CVE-\d{4}-\d{4,}|GHSA(?:-[0-9a-z]{4}){3}", re.I)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def norm(text: str) -> str:
    text = re.sub(r"[`*_\[\]()]", "", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def request(method, url, body=None, token=TOKEN):
    if not url.startswith("http"):
        url = API + url
    req = urllib.request.Request(
        url,
        method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
        return (json.loads(raw) if raw else None), resp.headers.get("Link", "")


# --------------------------------------------------------------------------- #
# CHANGELOG parsing
# --------------------------------------------------------------------------- #
BUILD_RE = re.compile(r"\b\d+\.\d+\.\d+\.\d+(?:\.\d+)?\b")
VERSION_START_RE = re.compile(r"\d+\.\d+")


def heading_version(title):
    """Return the version named in a heading, or None if it is not a release heading."""
    build = BUILD_RE.search(title)
    if build:  # e.g. "4.0.0.1900014 - 4.0.0 alpha 19.14"
        return build.group(0)
    start = VERSION_START_RE.search(title)
    if not start:
        return None
    rest = title[start.start():]  # e.g. "4.0.0 alpha 15.4 (outside ...) / 4.0.0 Voralpha ..."
    rest = re.split(r" / | - | \(|:", rest)[0]
    return re.sub(r"\s+", " ", rest).strip() or None


def parse_changelog(path):
    entries, version, security_section, current = [], None, False, None
    with open(path, encoding="utf-8") as f:
        for raw in f:
            line = raw.rstrip("\n")
            heading = re.match(r"^(#{1,6})\s+(.*)", line)
            if heading:
                level, title = len(heading.group(1)), heading.group(2).strip()
                current = None
                hv = heading_version(title) if level == 2 else None
                if level == 1:
                    version, security_section = None, False
                elif hv:
                    version, security_section = hv, False
                    if STOP_AT and version == STOP_AT:
                        break
                else:  # sub-section inside a release ("### Security fixes", "## Why ...")
                    security_section = "security" in title.lower()
                continue

            if not line.strip():
                continue  # blank lines keep the current entry (Impact:/Severity: follow)

            bullet = re.match(r"^[-*+]\s+(.*)", line)
            if bullet or current is None:
                text = (bullet.group(1) if bullet else line).strip()
                current = {
                    "version": version,
                    "first": text,
                    "text": text,
                    "security_section": security_section,
                }
                entries.append(current)
            else:  # indented sub-bullets, code, Impact:/Severity:/PoC paragraphs
                current["text"] += "\n" + line.strip()

    for e in entries:
        e["id"] = hashlib.sha1(f"{e['version']}|{norm(e['first'])}".encode()).hexdigest()[:16]
    return entries


def load_rejected():
    try:
        with open(REJECTED_CACHE, encoding="utf-8") as f:
            return {l.strip() for l in f if l.strip()}
    except FileNotFoundError:
        return set()


def save_rejected(ids):
    os.makedirs(os.path.dirname(REJECTED_CACHE) or ".", exist_ok=True)
    with open(REJECTED_CACHE, "w", encoding="utf-8") as f:
        f.write("\n".join(sorted(ids)) + "\n")


# --------------------------------------------------------------------------- #
# existing advisories
# --------------------------------------------------------------------------- #
def existing_advisories():
    out, url = [], f"/repos/{REPO}/security-advisories?per_page=100"
    while url:
        page, link = request("GET", url)
        out.extend(page or [])
        nxt = re.search(r'<([^>]+)>;\s*rel="next"', link)
        url = nxt.group(1) if nxt else None
    return out


def already_filed(entry, advisories):
    ids = {i.upper() for i in ID_RE.findall(entry["text"])}
    first = norm(entry["first"])
    for adv in advisories:
        blob = f"{adv.get('summary') or ''}\n{adv.get('description') or ''}"
        if f"{MARKER} {entry['id']}" in blob:
            return adv["ghsa_id"]
        if len(first) > 25 and first in norm(blob):
            return adv["ghsa_id"]
        known = {(adv.get("cve_id") or "").upper(), adv["ghsa_id"].upper()}
        if ids & known:
            return adv["ghsa_id"]
    return None


# --------------------------------------------------------------------------- #
# classification (GitHub Models, optional)
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You review CHANGELOG entries of a software project. Decide whether an entry "
    "discloses a fix for a security vulnerability IN THIS PROJECT ITSELF (not a "
    "routine dependency bump, not a memory leak, not a crash fix without security "
    "impact, not a warning about a third-party website). If the entry itself calls "
    "it a vulnerability or security fix, treat it as one. Use the entry's own "
    "Impact/Severity lines when present. Answer ONLY with JSON: "
    '{"is_vulnerability": bool, "summary": str (max 100 chars, advisory title), '
    '"severity": "low"|"medium"|"high"|"critical", "cwe_ids": ["CWE-123"], '
    '"details": str (2-5 sentences: impact, affected component, fix)}'
)


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [m.strip() for m in (os.environ.get("GEMINI_MODELS") or
                 "gemini-flash-latest,gemini-2.5-flash,gemini-flash-lite-latest").split(",") if m.strip()]
MODELS_EXHAUSTED = False  # GitHub Models daily quota used up -> go straight to Gemini


class RateLimited(RuntimeError):
    pass


def _chat(url, headers, payload, name):
    """POST one chat completion (same pattern as ai_check_fix.py); returns the answer text."""
    body = json.dumps(payload).encode()
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers={**headers, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                status, ctype, raw = resp.status, resp.headers.get("content-type", ""), resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read()[:300]
            if e.code == 429:
                wait = int(e.headers.get("retry-after") or 0)
                if 0 < wait <= 120 and attempt < 3:  # per-minute limit: wait it out
                    time.sleep(wait + 1)
                    continue
                raise RateLimited(f"{name} rate limit reached: {detail!r}")
            if e.code in (500, 502, 503) and attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"{name} error {e.code}: {detail!r}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < 3:
                time.sleep(10 * (attempt + 1))
                continue
            raise RuntimeError(f"{name} unreachable: {e}")
        try:
            data = json.loads(raw)
        except ValueError:
            if attempt < 2:  # empty/garbled body: try again
                time.sleep(5)
                continue
            raise RuntimeError(f"{name} sent no JSON (HTTP {status}, {ctype or 'no content-type'}): {raw[:200]!r}")
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        if not text.strip():
            if attempt < 2:
                time.sleep(5)
                continue
            raise RuntimeError(f"{name} sent an empty answer: {str(data)[:200]}")
        return text
    raise RuntimeError(f"{name}: no answer")


def _parse_answer(text):
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError(f"no JSON in the answer: {text[:200]!r}")
    return json.loads(match.group(0))


def ai_review(entry):
    """GitHub Models first (GITHUB_TOKEN); if it doesn't answer, Gemini (GEMINI_API_KEY)."""
    global MODELS_EXHAUSTED
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Project: {REPO}\nVersion: {entry['version']}\n"
                                    f"Entry:\n{entry['text'][:6000]}"},
    ]
    errors = []
    if MODELS_TOKEN and not MODELS_EXHAUSTED:
        try:
            text = _chat(
                "https://models.github.ai/inference/chat/completions",
                {"Authorization": f"Bearer {MODELS_TOKEN}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"},
                {"model": MODEL, "temperature": 0, "max_tokens": 800, "messages": messages},
                "GitHub Models",
            )
            return _parse_answer(text)
        except RateLimited as exc:
            MODELS_EXHAUSTED = True
            errors.append(str(exc))
            print(f"::warning::{exc} - using Gemini for the rest of this run")
        except (RuntimeError, ValueError) as exc:
            errors.append(str(exc))
            print(f"::warning::{exc}")
    if GEMINI_KEY:
        for model in GEMINI_MODELS:
            try:
                text = _chat(
                    GEMINI_URL, {"Authorization": f"Bearer {GEMINI_KEY}"},
                    # Gemini's "thinking" counts against max_tokens - leave room for it
                    {"model": model, "temperature": 0, "max_tokens": 800 + 8192, "messages": messages},
                    f"Gemini ({model})",
                )
                return _parse_answer(text)
            except (RuntimeError, ValueError) as exc:
                errors.append(str(exc))
    raise RuntimeError("; ".join(errors) or "no model available (GITHUB_TOKEN / GEMINI_API_KEY missing)")


class AIUnavailable(Exception):
    pass


def classify(entry):
    if not (entry["security_section"] or KEYWORDS.search(entry["text"])):
        return None
    info = {
        "is_vulnerability": True,
        "summary": re.sub(r"\s+", " ", entry["first"])[:100],
        "severity": "medium",
        "cwe_ids": [],
        "details": "Security fix disclosed in the project's changelog.",
    }
    if USE_AI:
        time.sleep(AI_DELAY)  # stay under the GitHub Models per-minute limit
        try:
            result = ai_review(entry)
        except Exception as exc:
            # Never create an unreviewed advisory: skip now, retry on the next run.
            raise AIUnavailable(str(exc)) from exc
        if not result.get("is_vulnerability"):
            return None
        info.update({k: v for k, v in result.items() if v})
    explicit = re.search(r"^Severity:\s*(low|medium|moderate|high|critical)", entry["text"], re.I | re.M)
    if explicit:
        sev = explicit.group(1).lower()
        info["severity"] = "medium" if sev == "moderate" else sev
    return info


# --------------------------------------------------------------------------- #
# advisory creation
# --------------------------------------------------------------------------- #
def build_payload(entry, info):
    version = entry["version"]
    quoted = "\n".join("> " + l for l in entry["text"].splitlines())
    description = (
        f"{info['details']}\n\n"
        f"### Patches\nFixed in version `{version}`. Please update.\n\n"
        f"### Changelog entry\n{quoted}\n\n"
        f"<sub>Created automatically from `{CHANGELOG}`. {MARKER} {entry['id']}</sub>\n"
    )
    payload = {
        "summary": info["summary"][:1024],
        "description": description,
        "vulnerabilities": [{
            "package": {"ecosystem": ECOSYSTEM, "name": PACKAGE},
            "vulnerable_version_range": f"< {version}",
            "patched_versions": version,
        }],
    }
    sev = str(info.get("severity", "")).lower()
    if sev in SEVERITIES:
        payload["severity"] = sev
    cwes = [c.upper() for c in info.get("cwe_ids") or [] if re.fullmatch(r"CWE-\d+", str(c), re.I)]
    if cwes:
        payload["cwe_ids"] = cwes
    return payload


def create(payload):
    try:
        adv, _ = request("POST", f"/repos/{REPO}/security-advisories", payload)
    except urllib.error.HTTPError as exc:
        if exc.code == 422 and "cwe_ids" in payload:  # unknown CWE -> retry without
            payload.pop("cwe_ids")
            adv, _ = request("POST", f"/repos/{REPO}/security-advisories", payload)
        else:
            raise
    if PUBLISH:
        request("PATCH", f"/repos/{REPO}/security-advisories/{adv['ghsa_id']}",
                {"state": "published"})
    return adv


# --------------------------------------------------------------------------- #
def main():
    if not TOKEN:
        sys.exit("ADVISORY_TOKEN is missing (see workflow comments).")

    entries = parse_changelog(CHANGELOG)
    advisories = existing_advisories()
    print(f"{len(entries)} changelog entries, {len(advisories)} existing advisories")

    rejected = load_rejected()
    report = []
    ai_failures, created_or_checked = 0, 0
    for entry in entries:
        if not entry["version"]:
            continue  # unreleased: no patched version yet
        if entry["id"] in rejected or already_filed(entry, advisories):
            continue
        try:
            info = classify(entry)
        except AIUnavailable as exc:
            print(f"  ! AI review failed for [{entry['version']}] {entry['first'][:60]}: {exc}")
            ai_failures += 1
            if ai_failures >= 5 and not created_or_checked:
                print("::error::AI review failed 5 times in a row - stopping; see warnings above.")
                break
            report.append(f"| {entry['version']} | {entry['first'][:80].replace('|', '/')} | - | skipped (AI unavailable, retried next run) |")
            continue
        ai_failures, created_or_checked = 0, created_or_checked + 1
        if not info:
            if USE_AI and (entry["security_section"] or KEYWORDS.search(entry["text"])):
                rejected.add(entry["id"])  # AI said "not a vulnerability": don't ask again
            continue

        print(f"- [{entry['version']}] {info['summary']}")
        payload = build_payload(entry, info)
        if DRY_RUN:
            report.append(f"| {entry['version']} | {info['summary']} | {payload.get('severity', '-')} | dry run |")
            continue
        try:
            adv = create(payload)
            advisories.append(adv)
            report.append(f"| {entry['version']} | {info['summary']} | {payload.get('severity', '-')} "
                          f"| [{adv['ghsa_id']}]({adv['html_url']}) |")
        except urllib.error.HTTPError as exc:
            msg = exc.read().decode(errors="replace")
            print(f"  ! failed: {exc.code} {msg}")
            report.append(f"| {entry['version']} | {info['summary']} | - | failed ({exc.code}) |")

    save_rejected(rejected)
    summary_file = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_file:
        with open(summary_file, "a", encoding="utf-8") as f:
            if report:
                f.write("## CHANGELOG → GHSA\n\n| Version | Summary | Severity | Advisory |\n|---|---|---|---|\n")
                f.write("\n".join(report) + "\n")
            else:
                f.write("## CHANGELOG → GHSA\n\nNo undisclosed vulnerabilities found. ✅\n")
    print(f"Done: {len(report)} advisory candidate(s).")


if __name__ == "__main__":
    main()
