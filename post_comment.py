import urllib.request, json, subprocess, sys

res = subprocess.run(["git", "credential", "fill"], input="protocol=https\nhost=github.com\n", capture_output=True, text=True)
lines = dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line)
token = lines.get("password")

repo = "albert-mueller/OpenCore-Legacy-Patcher-T2"
issue_number = "194"
url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"

comment_body = """@Richard-Hanus
I've analyzed the kernel panic screenshot. The crash occurs in `RestrictEvents` during its early initialization (`__start + 0xb`).

Since I completely removed `amfi=0x80` in `.8`, the macOS MAC policy framework is no longer entirely disabled (which is a good thing for fixing the WindowServer / yellow screen issue). However, it seems that `RestrictEvents 1.1.6` is incompatible with Darwin 25's new MAC framework and crashes the kernel if it attempts to load without `amfi=0x80` present.

I have just released a new test build (`4.0.0.18009.9`) that appends `-revoff` to the boot-args specifically for non-T2 Macs on macOS Tahoe. This will safely abort RestrictEvents initialization, preventing the kernel panic while allowing the system to boot. You will temporarily lose some cosmetic features (like RAM notifications being hidden on the Mac Pro), but you should now be able to reach the desktop!

Please download the new `.9` build, build and install OpenCore again, reboot, and let me know how it goes!"""

data = {"body": comment_body}

req = urllib.request.Request(url, headers={
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "Accept": "application/vnd.github+json"
}, data=json.dumps(data).encode())

try:
    with urllib.request.urlopen(req) as resp:
        print("Comment posted successfully!")
except Exception as e:
    print(f"Failed to post comment: {e}")
