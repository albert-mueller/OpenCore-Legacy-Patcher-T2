# OpenCore Legacy Patcher Privileged Helper Tool

`com.albert-mueller.opencore-patcher-t2.privileged-helper` is OpenCore Legacy Patcher's Privileged Helper Tool.

The architecture is as such:
1. The main application (OpenCore-Patcher-T2.app) will send arguments to the privileged helper tool to execute.
2. The privileged helper tool will check the code signature of the main application to ensure it is signed by Dortania.
3. The privileged helper tool will then execute the command and return the output to the main application.

The helper tool is able to execute code as root by using the "Set UID" bit present on the file.


## Running from source

Since running OpenCore Legacy Patcher from source will lack Dortania's code signature, you will need to disable code signature verification in the privileged helper tool otherwise root commands will fail.

To do so, compile the privileged helper tool with debug:
```
make debug
```

Then when you build OpenCore-Patcher-T2.pkg, the debug version of the helper tool will be used.


### Security Considerations

When using the Privileged Helper Tool from source, you are now adding a security risk to your system. By disabling the code signature checks, any malicious application is given ability to execute code as root.

If possible, we highly recommend creating a developer account with Apple and signing the application with your own ["Developer ID Application" certificate](https://developer.apple.com/help/account/create-certificates/create-developer-id-certificates/). This will allow you to run the application without disabling code signature checks.

* Note that Dortania's Team ID will need to be replaced in main.m with your own Team ID (`S74BDJXQMD` -> `YOUR_TEAM`)
* Additionally you will be required to compile OpenCore-Patcher-T2.app with your own Developer ID Application certificate

If this is not possible, we recommend using [OpenCore Legacy Patcher's prebuilt binaries](../../SOURCE.md) instead.

## Self signing the Privileged Helper Tool - preferred over make debug

A self signed release build keeps the caller check without paying for an Apple Developer ID.

### What the helper checks (release builds)

1. Its own signature must validate. The SHA-1 of its **leaf certificate** becomes the pin.
2. The **running** parent process must satisfy

   ```
   identifier "com.dortania.opencore-legacy-patcher-t2" and certificate leaf = H"<that SHA-1>"
   ```

   checked with `SecCodeCheckValidity` (running process) and `SecStaticCodeCheckValidity`
   (strict, on disk) - a modified app or a signature blob grafted onto another binary fails.
3. The caller must use the hardened runtime (otherwise error 172), so DYLD injection or a
   debugger cannot turn the genuine app into a client.

There is no identifier-only match. Re-signing the helper with another certificate moves the pin
with it - nothing to edit in `main.m`. To also hard-code the hash: `make CERT_SHA1=<sha1>`.
If you changed the app's bundle identifier: `make CLIENT_ID=<identifier>`.

### 1. Create the certificate (once)

```
./create-signing-certificate.sh
```

The identity goes into its own keychain, `~/Library/Keychains/oclp-signing.keychain-db`, with a
separate password - **not** the login keychain. Only `codesign` may use the key, the keychain
locks after 5 minutes idle and on sleep, and `Build-Project.command` locks it after every build.

Why: with a self signed certificate the private key *is* the trust root. Unlocked in the login
keychain, anything running as your user could sign a client the helper accepts. If the script
finds the certificate in the login keychain it stops; `--force` removes it there and creates a
new one (then rebuild and re-sign **both** the app and the helper).

Keep the key off the machine between builds:

```
./create-signing-certificate.sh --export /Volumes/USB/oclp-signing.p12   # offers to delete the keychain
./create-signing-certificate.sh --import /Volumes/USB/oclp-signing.p12   # before the next build
```

### 2. Build, sign, install

```
cd ci_tooling/privileged_helper_tool
make                          # release build - keeps the check
codesign -f -s "OCLP Self Signed" com.albert-mueller.opencore-patcher-t2.privileged-helper
sudo ./install.sh             # refuses unsigned/ad-hoc binaries, sets root:wheel 4755
```

macOS 10.15 and older: build x86_64 only and add `--timestamp=none` to `codesign`.

The app must be signed with the same certificate and the hardened runtime -
`Build-Project.command` does both.

### 3. Verify what you run

A self signed build is not notarized, so Gatekeeper still warns on first launch. Before you
accept that warning, check that it is your build:

```
./verify-signature.sh [app] [helper] [expected SHA-1]
```

It compares the leaf certificate SHA-1 of app and helper with yours (from the signing keychain,
or the hash printed when you created the certificate), runs the exact requirement the helper
enforces, and checks the hardened runtime and the helper's permissions.
