#!/usr/bin/env python3
"""Find websites that imitate this project's name or its GitHub Pages site,
and open a tracking issue in this repository for each one."""

import base64
import concurrent.futures as cf
import datetime
import html
import io
import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from typosquat_watch import MAX_DIST, fold, get, lev, paginate, post, skeleton, summary, variants  # noqa: E402

ORIGINAL = os.environ["ORIGINAL_REPO"]
DRY_RUN = os.environ.get("DRY_RUN", "true").lower() == "true"
SITE_URL = os.environ.get("SITE_URL", "").strip()
KEYWORDS = os.environ.get("KEYWORDS", "").split()
CT_KEYWORDS = os.environ.get("CT_KEYWORDS", "").split()
TLDS = os.environ.get("TLDS", "com net org io app dev").split()
MARKERS = [fold(m.strip()) for m in os.environ.get("CONTENT_MARKERS", "").split("|") if m.strip()]
ALLOWLIST = {h.lower() for h in os.environ.get("ALLOWLIST", "").split()}
# Newly registered domains: how many past days of registrations to check, and the
# names to look for in them (compared without dots, dashes or underscores)
NRD_DAYS = int(os.environ.get("NRD_DAYS", "3"))
NRD_KEYWORDS = [re.sub(r"[^a-z0-9]", "", k.lower()) for k in os.environ.get("NRD_KEYWORDS", "").split()]
# Content farms: sites under any domain name that scrape this project's releases, issues
# and PRs, often with fake "official" Dortania branding and ad overlays. They're found
# through a web search API (Brave Search), since their domains look nothing like ours.
# Without BRAVE_SEARCH_API_KEY this part is skipped.
SEARCH_QUERIES = [q.strip() for q in os.environ.get("SEARCH_QUERIES", "").split("|") if q.strip()]
BRAVE_KEY = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
SEARCH_RESULTS = min(int(os.environ.get("SEARCH_RESULTS", "20")), 20)
# Forums, news and video sites that discuss the project legitimately; never flagged as farms
FARM_ALLOWLIST = {h.lower() for h in os.environ.get("FARM_ALLOWLIST", "").split()}
# Script and iframe sources of ad networks typical for scraped "download" pages
AD_NETWORKS = ("googlesyndication.com", "adsbygoogle", "doubleclick.net", "adservice.google",
               "propellerads", "popads.net", "popcash.net", "adsterra", "monetag", "a-ads.com",
               "hilltopads", "clickadu", "exoclick", "juicyads", "ezoic", "mediavine", "taboola",
               "outbrain", "revcontent", "mgid.com", "adcash", "onclickads", "galaksion")
# Text that an honest page about this fork would contain
DISCLAIMERS = ("not affiliated", "unofficial", "independent community fork", "nicht mit dortania",
               "nicht offiziell", "inoffiziell")
LABEL = "typosquat-site"
MARKER = "<!-- typosquat-site:{} -->"
UA = f"Mozilla/5.0 (compatible; typosquat-site-watch; +https://github.com/{ORIGINAL})"
HOST_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
DOWNLOAD_EXT = re.compile(r"\.(dmg|pkg|zip|app|exe|iso)(\?|#|$)", re.I)
# A link or button also counts as a download when its label says so, even if the URL has
# no file extension (e.g. a "Download macOS" button pointing to a GitHub releases page,
# a file host or the site's own /download page)
DOWNLOAD_WORDS = re.compile(
    r"download|herunterladen|t[ée]l[ée]charger|descargar|scaricare|baixar|pobierz|indir|"
    r"скачать|завантажити|下载|下載|ダウンロード|다운로드", re.I)
DOWNLOAD_PATH = re.compile(r"/(?:releases|downloads?)(?:/|$)", re.I)
DOWNLOAD_ATTR = re.compile(r"(?:^|\s)download(?:\s*=|\s|/|$)", re.I)
CLICKABLE = re.compile(r"<(a|button)\b([^>]*)>(.*?)</\1\s*>", re.I | re.S)
URL_ATTR = re.compile(r"""\b(?:href|data-href|data-url|data-link|data-download|formaction)\s*=\s*(["'])(.*?)\1""",
                      re.I | re.S)
JS_URL = re.compile(r"""(?:location(?:\.href)?\s*=|location\.(?:assign|replace)\s*\(|window\.open\s*\()\s*["'`]([^"'`]+)["'`]""",
                    re.I)
LABEL_ATTR = re.compile(r"""\b(?:title|aria-label|alt|class|id|value)\s*=\s*(["'])(.*?)\1""", re.I | re.S)
TAG = re.compile(r"<[^>]+>")

socket.setdefaulttimeout(10)


# --------------------------------------------------------------- network --

def resolve(host):
    try:
        return sorted({ai[4][0] for ai in socket.getaddrinfo(host, 443)})
    except (OSError, UnicodeError):
        return []


def fetch(url, limit=2_000_000):
    """GET a page without running anything. Returns (status, final_url, html)."""
    for ctx in (ssl.create_default_context(), ssl._create_unverified_context()):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
        try:
            with urllib.request.urlopen(req, timeout=15, context=ctx) as r:
                ctype = r.headers.get("Content-Type", "")
                body = r.read(limit) if ("html" in ctype or "text" in ctype) else b""
                return r.status, r.geturl(), body.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, url, ""
        except urllib.error.URLError as e:
            if isinstance(e.reason, ssl.SSLError):
                continue  # retry once without certificate verification
            break
        except Exception:
            break
    return None, url, ""


def fetch_json(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ------------------------------------------------------------ discovery --

def site_info(owner, name):
    pages = get(f"/repos/{ORIGINAL}/pages") or {}
    url = SITE_URL or pages.get("html_url") or f"https://{owner}.github.io/{name}/"
    parts = urllib.parse.urlsplit(url)
    return url, parts.hostname.lower(), parts.path or "/"


# Latin letters that have a Cyrillic twin, for internationalized (IDN) lookalike domains
CYRILLIC_TWINS = {"a": "а", "c": "с", "e": "е", "i": "і", "j": "ј", "o": "о", "p": "р",
                  "s": "ѕ", "x": "х", "y": "у"}


def unpuny(host):
    """Show punycode labels (xn--...) the way browsers display them."""
    labels = []
    for label in host.split("."):
        if label.startswith("xn--"):
            try:
                label = label.encode("ascii").decode("idna")
            except (UnicodeError, ValueError):
                pass
        labels.append(label)
    return ".".join(labels)


def homograph_variants(keyword):
    """The keyword with Cyrillic lookalike letters, encoded as punycode labels."""
    kw = keyword.lower()
    spelled = {kw[:i] + CYRILLIC_TWINS[c] + kw[i + 1:] for i, c in enumerate(kw) if c in CYRILLIC_TWINS}
    spelled.add("".join(CYRILLIC_TWINS.get(c, c) for c in kw))
    out = set()
    for v in spelled - {kw}:
        try:
            out.add(v.encode("idna").decode("ascii"))
        except UnicodeError:
            pass
    return out


def domain_candidates(keywords):
    hosts = set()
    for kw in keywords:
        for v in variants(kw):
            if HOST_LABEL.match(v):
                hosts.update(f"{v}.{tld}" for tld in TLDS)
        for v in homograph_variants(kw):
            hosts.update(f"{v}.{tld}" for tld in TLDS)
    return hosts


def ct_hosts(keyword):
    """Hostnames from certificate-transparency logs that contain the keyword."""
    q = urllib.parse.quote(f"%{keyword}%")
    url = f"https://crt.sh/?q={q}&output=json&exclude=expired&deduplicate=Y"
    for attempt in range(3):
        try:
            rows = fetch_json(url, timeout=90)
            names = set()
            for row in rows:
                for n in (row.get("name_value") or "").split("\n"):
                    n = n.strip().lower().lstrip("*.")
                    if n and "@" not in n:
                        names.add(n)
            return names
        except Exception as e:
            print(f"  crt.sh attempt {attempt + 1} for '{keyword}' failed: {e}")
            time.sleep(15)
    print(f"::warning::Certificate-transparency lookup for '{keyword}' failed; skipped this run.")
    return set()


def github_pages_candidates(site_host, site_path):
    """Lookalike <user>.github.io accounts that actually exist."""
    if not site_host.endswith(".github.io"):
        return {}
    user = site_host.split(".")[0]
    found = {}
    for v in variants(user):
        if v != user and HOST_LABEL.match(v) and get(f"/users/{v}"):
            found[f"{v}.github.io"] = site_path
    return found


def nrd_domains(days):
    """Domains registered in the last `days` days, from WhoisDS's free daily lists."""
    domains = set()
    today = datetime.datetime.now(datetime.timezone.utc).date()
    for back in range(1, days + 1):
        day = (today - datetime.timedelta(days=back)).isoformat()
        token = base64.b64encode(f"{day}.zip".encode()).decode()
        url = f"https://whoisds.com/whois-database/newly-registered-domains/{token}/nrd"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                for name in z.namelist():
                    domains.update(line.strip().lower()
                                   for line in z.read(name).decode("ascii", "ignore").split())
        except Exception as e:
            print(f"::warning::Newly registered domain list for {day} unavailable: {e}")
    print(f"{len(domains)} newly registered domains checked")
    return domains


def nrd_match(domain):
    """True if the domain's name contains, or is a near-miss of, one of NRD_KEYWORDS."""
    label = re.sub(r"[^a-z0-9]", "", fold(unpuny(domain).split(".")[0]))
    for kw in NRD_KEYWORDS:
        if kw in label:
            return True
        dist = MAX_DIST if len(kw) >= 8 else 1  # short names only allow one typo
        if abs(len(label) - len(kw)) <= dist and (
                skeleton(label) == skeleton(kw) or lev(label, kw) <= dist):
            return True
    return False


# ------------------------------------------------------------ inspection --

def link_target(url, base):
    """Absolute URL of a link, or None for in-page, mail and script-only links."""
    url = html.unescape(url).strip()
    if not url or url.lower().startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
        return None
    return urllib.parse.urljoin(base, url)


def describe_host(url):
    """Where a link leads: its host, plus the account for GitHub links."""
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "?").lower()
    if host in ("github.com", "www.github.com"):
        owner = parts.path.strip("/").split("/")[0]
        return f"github.com/{owner}" if owner else "github.com"
    return host


def download_links(page, base):
    """URLs of the links and buttons on a page that offer a download.

    A link counts if it points at an installer file, carries the `download`
    attribute, leads to a releases/download page, or is labelled as a download
    ("Download macOS", an icon with alt="Download", class="btn-download", ...),
    wherever it points to, GitHub included."""
    found = set()
    for _tag, attrs, inner in CLICKABLE.findall(page):
        urls = [u for _q, u in URL_ATTR.findall(attrs)] + JS_URL.findall(html.unescape(attrs))
        urls = [u for u in (link_target(u, base) for u in urls) if u]
        if not urls:
            continue
        label = " ".join([TAG.sub(" ", inner)] + [v for _q, v in LABEL_ATTR.findall(attrs + " " + inner)])
        labelled = bool(DOWNLOAD_WORDS.search(fold(html.unescape(label))) or DOWNLOAD_ATTR.search(attrs))
        for u in urls:
            if labelled or DOWNLOAD_EXT.search(u) or DOWNLOAD_PATH.search(urllib.parse.urlsplit(u).path):
                found.add(u)
    # Anything else on the page that points straight at an installer file
    for _q, u in URL_ATTR.findall(page):
        u = link_target(u, base)
        if u and DOWNLOAD_EXT.search(u):
            found.add(u)
    return found


def allowed(host, site_host):
    return host == site_host or any(host == a or host.endswith("." + a) for a in ALLOWLIST)


def inspect(host, path):
    ips = resolve(host)
    if not ips:
        return None
    status, final, page = fetch(f"https://{host}{path}")
    if not page:
        status, final, page = fetch(f"http://{host}{path}")
    if not page:
        return None
    low = fold(page)
    hits = [m for m in MARKERS if m in low]
    if not hits:
        return None  # resolves, but doesn't present itself as this project
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    dl_hosts = sorted({describe_host(u) for u in download_links(page, final)})
    return {
        "host": host, "url": final, "status": status, "ips": ips,
        "title": re.sub(r"\s+", " ", title.group(1)).strip()[:150] if title else "",
        "markers": hits, "download_hosts": dl_hosts,
        "links_official": f"github.com/{ORIGINAL}".lower() in low,
    }


def rdap(host):
    """Registrar, abuse contact and registration date (best effort)."""
    domain = ".".join(host.split(".")[-2:])
    try:
        data = fetch_json(f"https://rdap.org/domain/{domain}")
    except Exception:
        return {}

    def vcard(entity, key):
        for item in (entity.get("vcardArray") or [None, []])[1]:
            if item and item[0] == key:
                return item[3]
        return None

    info = {}
    for ent in data.get("entities", []):
        if "registrar" in ent.get("roles", []):
            info["registrar"] = vcard(ent, "fn")
            for sub in ent.get("entities", []):
                if "abuse" in sub.get("roles", []):
                    info["abuse"] = vcard(sub, "email")
    for ev in data.get("events", []):
        if ev.get("eventAction") == "registration":
            info["registered"] = ev.get("eventDate", "")[:10]
    return info


# --------------------------------------------------------- content farms --

def brave_search(query):
    """Result URLs for one query from the Brave Search API."""
    q = urllib.parse.urlencode({"q": query, "count": SEARCH_RESULTS, "safesearch": "off"})
    req = urllib.request.Request(f"https://api.search.brave.com/res/v1/web/search?{q}", headers={
        "Accept": "application/json", "X-Subscription-Token": BRAVE_KEY, "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read())
    except Exception as e:
        print(f"::warning::Web search for '{query}' failed: {e}")
        return []
    return [res.get("url") for res in (data.get("web") or {}).get("results", []) if res.get("url")]


def farm_candidates(site_host):
    """One result URL per host from the web search, minus allowlisted hosts."""
    if not SEARCH_QUERIES:
        return {}
    if not BRAVE_KEY:
        print("::notice::BRAVE_SEARCH_API_KEY not set; content-farm search skipped.")
        return {}
    urls = {}
    for query in SEARCH_QUERIES:
        for url in brave_search(query):
            host = (urllib.parse.urlsplit(url).hostname or "").lower()
            if not host or allowed(host, site_host) or any(
                    host == a or host.endswith("." + a) for a in FARM_ALLOWLIST):
                continue
            urls.setdefault(host, url)
        time.sleep(1.2)  # free plan: about one request per second
    print(f"{len(urls)} hosts from web search to check for scraped content")
    return urls


def inspect_farm(host, url):
    """A search hit counts as a content farm when it shows this project's name AND at
    least one sign of misleading users: fake Dortania branding without a disclaimer,
    ad networks, or download buttons that lead away from GitHub."""
    status, final, page = fetch(url)
    if not page:
        return None
    low = fold(page)
    hits = [m for m in MARKERS if m in low]
    if not hits:
        return None
    ads = sorted({a for a in AD_NETWORKS if a in low})
    brand_claim = "dortania" in low and not any(d in low for d in DISCLAIMERS)
    dl_hosts = sorted({describe_host(u) for u in download_links(page, final)})
    foreign_dl = [h for h in dl_hosts if not (h.startswith("github.com") or h == host)]
    if not (ads or brand_claim or foreign_dl):
        return None  # mentions the project, but nothing points to deception
    title = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
    return {
        "host": host, "url": final, "status": status, "ips": resolve(host),
        "title": re.sub(r"\s+", " ", html.unescape(title.group(1))).strip()[:150] if title else "",
        "markers": hits, "download_hosts": dl_hosts,
        "links_official": f"github.com/{ORIGINAL}".lower() in low,
        "farm": True, "ads": ads, "brand_claim": brand_claim,
    }


# ----------------------------------------------------------------- texts --

def farm_issue_body(r, reg):
    contact = f" ({reg['abuse']})" if reg.get("abuse") else ""
    signs = []
    if r["brand_claim"]:
        signs.append("mentions **Dortania** without saying this is an independent, unofficial fork "
                     "(possible fake official branding)")
    if r["ads"]:
        signs.append("loads ad networks: " + ", ".join(f"`{a}`" for a in r["ads"]))
    off = [h for h in r["download_hosts"] if not h.startswith("github.com")]
    if off:
        signs.append("download links lead away from GitHub: " + ", ".join(off))
    lines = [
        "Found a page that republishes content about this project and shows signs of misleading users: "
        f"**{r['host']}**",
        "",
        f"- **URL:** {r['url']} (HTTP {r['status']})" if r["status"] else f"- **URL:** {r['url']}",
        f"- **Page title:** {r['title'] or '—'}",
        f"- **Matched text:** {', '.join(f'`{m}`' for m in r['markers'])}",
        f"- **Links to the official repo:** {'yes' if r['links_official'] else 'no'}",
        f"- **Download links point to:** {', '.join(r['download_hosts']) or 'no download links'}",
        f"- **IP addresses:** {', '.join(r['ips']) or '—'}",
    ]
    if reg:
        lines += [
            f"- **Registrar:** {reg.get('registrar') or '—'}",
            f"- **Registrar abuse contact:** {reg.get('abuse') or '—'}",
            f"- **Registered:** {reg.get('registered') or '—'}",
        ]
    lines += ["", "**Signs found:**"] + [f"- {x}" for x in signs] + [
        "",
        "These checks are heuristics: a blog post or mirror can trip them too. Please look at the page first.",
        "",
        "**If it's a deceptive content farm:** if downloads on it are malicious, report it to "
        "[Google Safe Browsing](https://safebrowsing.google.com/safebrowsing/report_badware/); "
        f"report fake branding or scraped content to the hosting provider and the registrar's abuse contact{contact}, "
        "and policy violations to the ad network it uses.",
        "",
        "Closing this issue marks the site as handled, and the workflow won't file it again.",
        "",
        MARKER.format(r["host"]),
    ]
    return "\n".join(lines)


def issue_body(r, reg):
    if r["host"].endswith(".github.io"):
        account = r["host"].split(".")[0]
        where = f"GitHub Pages site of the account [`{account}`](https://github.com/{account})"
        action = ("Report the account or its Pages repository to GitHub "
                  "(**Report repository** / **Block or report** on their profile).")
    else:
        where = "independent domain"
        contact = f" ({reg['abuse']})" if reg.get("abuse") else ""
        action = ("If it impersonates the project, report it to the registrar's abuse contact"
                  f"{contact} and the hosting provider, and submit it to "
                  "[Google Safe Browsing](https://safebrowsing.google.com/safebrowsing/report_phish/).")
    shown = unpuny(r["host"])
    name = f"**{r['host']}**" + (f" (shown in browsers as **{shown}**)" if shown != r["host"] else "")
    if r["markers"]:
        intro = f"Found a website with a lookalike name that presents itself as this project: {name}"
    else:
        intro = (f"Found a newly registered domain with a lookalike name: {name}\n\n"
                 "It doesn't show project content yet. That's normal for a domain that was just registered; "
                 "impersonation sites are often set up a few days later, so it's worth keeping an eye on.")
    status = f"HTTP {r['status']}" if r["status"] else "no response yet"
    lines = [
        intro,
        "",
        f"- **URL:** {r['url']} ({status})",
        f"- **Newly registered:** {'yes, in the last ' + str(NRD_DAYS) + ' days' if r.get('new_registration') else 'not in the recent registration lists'}",
        f"- **Type:** {where}",
        f"- **Page title:** {r['title'] or '—'}",
        f"- **Matched text:** {', '.join(f'`{m}`' for m in r['markers']) or 'none yet'}",
        f"- **Links to the official repo:** {'yes' if r['links_official'] else 'no'}",
        f"- **Download links point to:** {', '.join(r['download_hosts']) or 'no download links'}",
        f"- **IP addresses:** {', '.join(r['ips']) or 'none (not resolving yet)'}",
    ]
    if reg:
        lines += [
            f"- **Registrar:** {reg.get('registrar') or '—'}",
            f"- **Registrar abuse contact:** {reg.get('abuse') or '—'}",
            f"- **Registered:** {reg.get('registered') or '—'}",
        ]
    lines += [
        "",
        "A matching name and project text don't prove bad intent; it may be a fan page or a mirror. "
        "Please check the site before acting.",
        "",
        f"**If it's an impersonation:** {action}",
        "",
        "Closing this issue marks the site as handled, and the workflow won't file it again.",
        "",
        MARKER.format(r["host"]),
    ]
    return "\n".join(lines)


# ------------------------------------------------------------------ main --

def main():
    repo = get(f"/repos/{ORIGINAL}")
    owner, name = repo["owner"]["login"], repo["name"]
    site_url, site_host, site_path = site_info(owner, name)
    keywords = KEYWORDS or [name.lower(), name.lower().replace("-", "")]

    summary(f"## Lookalike site scan for `{site_url}`" + (" (dry run)" if DRY_RUN else ""))

    targets = {h: "/" for h in domain_candidates(keywords)}
    for kw in CT_KEYWORDS:
        for h in ct_hosts(kw):
            targets.setdefault(h, "/")
    targets.update(github_pages_candidates(site_host, site_path))
    new_regs = {d for d in nrd_domains(NRD_DAYS) if nrd_match(d)} if NRD_KEYWORDS else set()
    for h in new_regs:
        targets.setdefault(h, "/")
    targets = {h: p for h, p in targets.items() if not allowed(h, site_host)}
    print(f"{len(targets)} hostnames to check")

    with cf.ThreadPoolExecutor(max_workers=48) as ex:
        results = [r for r in ex.map(lambda hp: inspect(*hp), targets.items()) if r]

    # Content farms under unrelated domain names, found through web search
    farm_urls = {h: u for h, u in farm_candidates(site_host).items()
                 if h not in {r["host"] for r in results}}
    with cf.ThreadPoolExecutor(max_workers=16) as ex:
        results += [r for r in ex.map(lambda hu: inspect_farm(*hu), farm_urls.items()) if r]

    # A lookalike domain registered in the last few days counts even before it shows
    # any content, so it's caught before it goes live.
    found_hosts = {r["host"] for r in results}
    for r in results:
        r["new_registration"] = r["host"] in new_regs
    for host in sorted(new_regs - found_hosts):
        if allowed(host, site_host):
            continue
        results.append({
            "host": host, "url": f"http://{host}/", "status": None, "ips": resolve(host),
            "title": "", "markers": [], "download_hosts": [], "links_official": False,
            "new_registration": True,
        })

    tracked = set()
    for issue in paginate(f"/repos/{ORIGINAL}/issues?labels={LABEL}&state=all&per_page=100"):
        m = re.search(r"<!-- typosquat-site:(\S+) -->", issue.get("body") or "")
        if m:
            tracked.add(m.group(1).lower())

    new = [r for r in sorted(results, key=lambda r: r["host"]) if r["host"] not in tracked]
    if new and not DRY_RUN:
        post(f"/repos/{ORIGINAL}/labels",
             {"name": LABEL, "color": "b60205", "description": "Website imitating this project"})

    for r in new:
        summary(f"- **{r['host']}** — {r['title'] or 'no title'}; matched "
                + (", ".join(f"`{m}`" for m in r["markers"]) or "nothing yet (newly registered)"))
        if DRY_RUN:
            continue
        reg = {} if r["host"].endswith(".github.io") else rdap(r["host"])
        farm = r.get("farm")
        status, _ = post(f"/repos/{ORIGINAL}/issues", {
            "title": f"{'Content farm' if farm else 'Lookalike site'}: {r['host']}",
            "body": farm_issue_body(r, reg) if farm else issue_body(r, reg),
            "labels": [LABEL],
        })
        if status >= 300:
            print(f"::error::Could not open an issue for {r['host']} (HTTP {status})")
        time.sleep(3)

    summary(f"\n{len(new)} new lookalike sites found ({len(results)} matching in total).")


if __name__ == "__main__":
    main()
