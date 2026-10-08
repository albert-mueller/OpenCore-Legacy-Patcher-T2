# OpenCore Legacy Patcher Privileged Helper Tool

`com.albert-mueller.opencore-patcher-t2.privileged-helper` is OpenCore Legacy Patcher's Privileged Helper Tool.

The architecture is as such:
1. The main application (OpenCore-Patcher-T2.app) will send arguments to the privileged helper tool to execute.
2. The privileged helper tool checks the code signature of the calling process: it must be the app (identifier
   `com.dortania.opencore-legacy-patcher-t2`), signed with exactly the certificate the helper itself is signed with,
   validly signed and using the hardened runtime (see "What the helper checks" below).
3. The privileged helper tool will then execute the command and return the output to the main application.

The helper tool is able to execute code as root by using the "Set UID" bit present on the file.


## Running from source

Since running OpenCore Legacy Patcher from source will lack a code signature the helper accepts, root commands
through the helper will fail. The app then falls back to asking for your administrator password.

Alternatively, compile the privileged helper tool with debug:
```
make debug
```

Then when you build OpenCore-Patcher-T2.pkg, the debug version of the helper tool will be used.


### Security Considerations

A DEBUG helper skips the caller check entirely: any process running as your user can use it to run the commands on
its allowlist as root. Only use it on a test machine.

Prefer a signed release build instead - either with your own Apple ["Developer ID Application"
certificate](https://developer.apple.com/help/account/create-certificates/create-developer-id-certificates/), or with
a free self signed certificate (next section). Nothing has to be changed in `main.m` for either: the helper pins
whatever certificate it is signed with.

If this is not possible, we recommend using [OpenCore Legacy Patcher's prebuilt binaries](../../SOURCE.md) instead.


## Self signing the Privileged Helper Tool - preferred over make debug

A self signed release build keeps the caller check without paying for an Apple Developer ID.

### What the helper checks (release builds)

1. Its own signature must validate. The SHA-1 of its **leaf certificate** becomes the pin.
2. The **running** parent process must satisfy

   ```
   identifier "com.dortania.opencore-legacy-patcher-t2" and certificate leaf = H"<that SHA-1>"
   ```

   checked with `SecCodeCheckValidity` (running process) and `SecStaticCodeCheckValidity` (strict, on disk) - a
   modified app or a signature blob grafted onto another binary fails.
3. The caller must use the hardened runtime (otherwise error 172), so DYLD injection or a debugger cannot turn the
   genuine app into a client.

So **app and helper must be signed with the same certificate**, and the app with the hardened runtime.

### 0. Requirements (once)

```
xcode-select --install          # clang, codesign, make - skip if Xcode is installed
```

`openssl` (LibreSSL, part of macOS) is enough for the certificate script.

### 1. Create the certificate (once)

```
cd ci_tooling/privileged_helper_tool
./create-signing-certificate.sh
```

The script asks for three things: a **new password** for the signing keychain (choose one that is *not* your login
password), the same password again to unlock it, and once more so `codesign` may use the key.

The identity goes into its own keychain, `~/Library/Keychains/oclp-signing.keychain-db` - **not** the login keychain.
Only `codesign` may use the key, the keychain locks after 5 minutes idle and on sleep, and `Build-Project.command`
locks it after every build.

At the end it prints the certificate's **SHA-1**. Write it down: `verify-signature.sh` compares against it, and you
can hard-code it into the helper (step 2).

Why a separate keychain: with a self signed certificate the private key *is* the trust root. Unlocked in the login
keychain, anything running as your user could sign a client the helper accepts. If the script finds the certificate
in the login (or System) keychain it stops; `./create-signing-certificate.sh --force` removes it there and creates a
new one.

Check that exactly one identity is found:

```
security find-identity -v -p codesigning | grep "OCLP Self Signed"
```

If it shows up twice, `codesign` reports "ambiguous" - run `./create-signing-certificate.sh --force`, or sign with
the SHA-1 instead of the name (`codesign -s <SHA-1> ...`).

### 2. Build the helper

```
make                                   # release build - keeps the caller check
```

Optional:

```
make CERT_SHA1=<SHA-1 from step 1>     # also hard-code your certificate into the binary
make ARCHS="-arch x86_64"              # Xcode 11 and older (no arm64 support)
make CLIENT_ID=<bundle identifier>     # only if you changed the app's bundle identifier
```

`make debug` is **not** what you want here - it disables the check.

### 3a. Sign everything with Build-Project.command (recommended)

From the repository root:

```
python3 Build-Project.command
```

It signs the helper you just built **and** the app with the same identity and the hardened runtime, then builds the
packages. macOS asks for the signing keychain password when `codesign` first needs the key (the keychain is locked
again afterwards). If you have more than one code signing identity, pick it explicitly:

```
python3 Build-Project.command --application-signing-identity "OCLP Self Signed"
```

A self signed *code signing* certificate cannot sign installer packages (that needs a "Developer ID Installer"
certificate), so the `.pkg` files stay unsigned - leave `--installer-signing-identity` empty.

Install `dist/OpenCore-Patcher-T2.pkg`. It puts the app into `/Library/Application Support/albert-mueller/` and the
helper into `/Library/PrivilegedHelperTools/` with the setuid bit.

### 3b. Sign and install the helper by hand (helper only)

Only needed if you rebuild the helper without rebuilding the app. The app must already be signed with the same
certificate, or the helper will refuse it (error 167).

```
security unlock-keychain ~/Library/Keychains/oclp-signing.keychain-db
codesign -f -s "OCLP Self Signed" --options runtime com.albert-mueller.opencore-patcher-t2.privileged-helper
security lock-keychain ~/Library/Keychains/oclp-signing.keychain-db
codesign --verify --strict -vv com.albert-mueller.opencore-patcher-t2.privileged-helper
sudo ./install.sh                      # refuses unsigned/ad-hoc binaries, sets root:wheel 4755
```

macOS 10.15 and older: add `--timestamp=none` to `codesign`.

Signing the app by hand works the same way, but needs `--deep`, the hardened runtime and the entitlements:

```
codesign -f --deep -s "OCLP Self Signed" --options runtime \
  --entitlements ../entitlements/entitlements.plist ../../dist/OpenCore-Patcher-T2.app
```

### 4. Verify what you run

A self signed build is not notarized, so Gatekeeper still warns on first launch. Before you accept that warning,
check that it is your build:

```
./verify-signature.sh                                  # installed app + helper, SHA-1 from the signing keychain
./verify-signature.sh <app> <helper> <SHA-1>           # explicit paths / SHA-1 (e.g. after --export)
```

It compares the leaf certificate SHA-1 of app and helper with yours, runs the exact requirement the helper enforces,
and checks the hardened runtime and the helper's permissions. Only if it ends with "All checks passed", open the app:
right-click > Open, or on macOS 15 and newer System Settings > Privacy & Security > "Open Anyway".

### 5. Afterwards

Keep the key off the machine between builds (optional, recommended):

```
./create-signing-certificate.sh --export /Volumes/USB/oclp-signing.p12   # offers to delete the keychain
./create-signing-certificate.sh --import /Volumes/USB/oclp-signing.p12   # before the next build
```

The SHA-1 stays the same after export/import, so app and helper keep working.

If you **replace** the certificate (`--force`), the SHA-1 changes: rebuild and re-sign **both** the helper and the
app and reinstall them. An old helper refuses a newly signed app and vice versa.

Maintainers: commit the rebuilt helper binary, otherwise the packaged builds keep using the old one.

### Troubleshooting

| Helper exit code | Meaning | Fix |
|---|---|---|
| 161 / 162 | setuid bit missing / not effective | `sudo ./install.sh`, or reinstall the package |
| 165 | the helper itself is unsigned, ad-hoc signed or its signature is broken | sign it (3a/3b) and reinstall |
| 167 | the app is not signed with the helper's certificate, has another identifier, or was modified | re-sign app and helper with the same identity; `./verify-signature.sh` shows which side differs |
| 172 | the app was signed without the hardened runtime | re-sign the app with `--options runtime` (Build-Project.command does this) |

On 165, 167 and 172 the app stops using the helper for the rest of the session and asks for the administrator
password instead.
