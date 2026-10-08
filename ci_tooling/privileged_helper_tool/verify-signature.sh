#!/bin/zsh --no-rcs
# ------------------------------------------------------
# verify-signature.sh
# ------------------------------------------------------
# Self signed builds are not notarized, so Gatekeeper
# warns on first launch. This tells you whether the app
# and helper you are about to run are the ones YOU signed:
#   - both signatures are valid (codesign --verify --strict)
#   - both carry the same leaf certificate
#   - that is your certificate (signing keychain, or the
#     SHA-1 you pass / wrote down when creating it)
#   - the app satisfies the exact requirement the helper
#     enforces, incl. the hardened runtime
#   - the helper is root:wheel 4755
#
# Usage:
#   ./verify-signature.sh [app] [helper] [expected SHA-1]
#   OCLP_CERT_SHA1=<sha1> ./verify-signature.sh
# ------------------------------------------------------

CERT_NAME="${OCLP_CERT_NAME:-OCLP Self Signed}"
CLIENT_ID="${OCLP_CLIENT_ID:-com.dortania.opencore-legacy-patcher-t2}"
SIGNING_KEYCHAIN="${HOME}/Library/Keychains/oclp-signing.keychain-db"

appPath="${1:-/Library/Application Support/albert-mueller/OpenCore-Patcher-T2/OpenCore-Patcher-T2.app}"
helperPath="${2:-/Library/PrivilegedHelperTools/com.albert-mueller.opencore-patcher-t2.privileged-helper}"
expectedSHA1="${3:-$OCLP_CERT_SHA1}"
expectedSHA1="${expectedSHA1:u}"

failed=0
function _fail() { echo "[!] $1"; failed=1 }

function _leafSHA1() {
    local tmpDir=$(/usr/bin/mktemp -d)
    /usr/bin/codesign -d --extract-certificates="$tmpDir/cert" "$1" >/dev/null 2>&1
    [[ -f "$tmpDir/cert0" ]] && /usr/bin/shasum -a 1 "$tmpDir/cert0" | /usr/bin/awk '{ print toupper($1) }'
    /bin/rm -rf "$tmpDir"
}

for label target in "App   " "$appPath" "Helper" "$helperPath"; do
    if [[ ! -e "$target" ]]; then
        _fail "$label not found: $target"
    elif /usr/bin/codesign --verify --strict --deep "$target" 2>/dev/null; then
        echo "[ok] $label signature valid"
    else
        _fail "$label signature INVALID or missing: $target"
    fi
done

appSHA1=$(_leafSHA1 "$appPath")
helperSHA1=$(_leafSHA1 "$helperPath")

if [[ -z "$expectedSHA1" && -f "$SIGNING_KEYCHAIN" ]]; then
    expectedSHA1=$(/usr/bin/security find-certificate -c "$CERT_NAME" -Z "$SIGNING_KEYCHAIN" 2>/dev/null \
        | /usr/bin/awk '/SHA-1 hash:/ { print toupper($3); exit }')
fi

echo
echo "Leaf certificate SHA-1"
echo "  app:      ${appSHA1:-<none>}"
echo "  helper:   ${helperSHA1:-<none>}"
echo "  expected: ${expectedSHA1:-<unknown - pass it as 3rd argument or OCLP_CERT_SHA1>}"
echo

[[ -z "$appSHA1" || "$appSHA1" != "$helperSHA1" ]] && \
    _fail "App and helper are NOT signed with the same certificate - the helper will refuse the app."
if [[ -n "$expectedSHA1" ]]; then
    [[ "$helperSHA1" != "$expectedSHA1" ]] && _fail "Helper is NOT signed with your certificate. Do not use it."
    [[ "$appSHA1"    != "$expectedSHA1" ]] && _fail "App is NOT signed with your certificate. Do not run it."
else
    _fail "No expected SHA-1 to compare against - cannot tell whether this is your build."
fi

# Same requirement the helper enforces at runtime
if [[ -n "$helperSHA1" ]] && /usr/bin/codesign --verify --strict \
        -R="identifier \"$CLIENT_ID\" and certificate leaf = H\"$helperSHA1\"" "$appPath" 2>/dev/null; then
    echo "[ok] App satisfies the helper's requirement (identifier $CLIENT_ID + leaf pin)"
else
    _fail "App does NOT satisfy: identifier \"$CLIENT_ID\" and certificate leaf = H\"$helperSHA1\""
fi

if /usr/bin/codesign -dvv "$appPath" 2>&1 | /usr/bin/grep -q "flags=.*runtime"; then
    echo "[ok] App uses the hardened runtime"
else
    _fail "App does not use the hardened runtime - the helper will refuse it (error 172)."
fi

helperMode=$(/usr/bin/stat -f "%Su:%Sg %Lp" "$helperPath" 2>/dev/null)
if [[ "$helperMode" == "root:wheel 4755" ]]; then
    echo "[ok] Helper permissions: $helperMode"
else
    _fail "Helper permissions are \"$helperMode\", expected \"root:wheel 4755\""
fi

echo
if [[ $failed -eq 0 ]]; then
    echo "All checks passed - this is your own build. The Gatekeeper warning is expected (not notarized)."
else
    echo "Some checks failed - see above."
fi
exit $failed
