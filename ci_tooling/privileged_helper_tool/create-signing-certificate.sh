#!/bin/bash
#
# create-signing-certificate.sh
#
# Erstellt ein selbstsigniertes Code-Signing-Zertifikat fuer den lokalen Build
# von OpenCore-Patcher-T2 - in einem EIGENEN, gesperrten Schluesselbund
# (nicht im Anmeldeschluesselbund).
#
# Creates a self signed code signing certificate for local builds of
# OpenCore-Patcher-T2 - in its OWN, locked keychain (not the login keychain).
#
# Warum / why: the privileged helper trusts exactly this certificate. With a
# self signed certificate the private key IS the trust root - unlocked in the
# login keychain, any process running as your user could sign a client the
# helper accepts.
#
# Nutzung / Usage:
#   ./create-signing-certificate.sh                     create (once)
#   ./create-signing-certificate.sh --name "Name"       custom certificate name
#   ./create-signing-certificate.sh --force             replace existing ones (incl. login keychain)
#   ./create-signing-certificate.sh --export key.p12    export, then remove the keychain from this Mac
#   ./create-signing-certificate.sh --import key.p12    bring an exported key back for a build
#

set -euo pipefail

CERT_NAME="OCLP Self Signed"
VALID_DAYS=3650
FORCE=0
MODE="create"
P12_FILE=""

LOGIN_KEYCHAIN="${HOME}/Library/Keychains/login.keychain-db"
SYSTEM_KEYCHAIN="/Library/Keychains/System.keychain"
SIGNING_KEYCHAIN="${HOME}/Library/Keychains/oclp-signing.keychain-db"
AUTOLOCK_SECONDS=300

# ---------------------------------------------------------------- Argumente --

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)   CERT_NAME="${2:?--name requires a value}"; shift 2 ;;
        --days)   VALID_DAYS="${2:?--days requires a value}"; shift 2 ;;
        --force)  FORCE=1; shift ;;
        --export) MODE="export"; P12_FILE="${2:?--export requires a file}"; shift 2 ;;
        --import) MODE="import"; P12_FILE="${2:?--import requires a file}"; shift 2 ;;
        -h|--help) sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "[!] Unbekannte Option / unknown option: $1" >&2; exit 1 ;;
    esac
done

if [[ "$(uname)" != "Darwin" ]]; then
    echo "[!] Nur macOS / macOS only"; exit 1
fi

# ----------------------------------------------------------------- Helfer --

# Signing keychain in the user search list, so codesign and
# 'security find-identity' (Build-Project.command) find the identity.
add_to_search_list() {
    local current=()
    while IFS= read -r line; do
        line="${line#"${line%%[![:space:]]*}"}"   # trim leading whitespace
        line="${line%\"}"; line="${line#\"}"
        [[ -n "$line" && "$line" != "${SIGNING_KEYCHAIN}" ]] && current+=("$line")
    done < <(security list-keychains -d user)
    security list-keychains -d user -s "${current[@]}" "${SIGNING_KEYCHAIN}"
}

ensure_signing_keychain() {
    if [[ ! -f "${SIGNING_KEYCHAIN}" ]]; then
        echo "--- Neuer Schluesselbund / new keychain: ${SIGNING_KEYCHAIN}"
        echo "    Eigenes Passwort waehlen (NICHT das Anmeldepasswort)"
        echo "    Choose a separate password (NOT your login password)"
        security create-keychain "${SIGNING_KEYCHAIN}"
    fi
    # -l: lock on sleep, -t: lock after N seconds idle (no -u: never 'stay unlocked')
    security set-keychain-settings -l -t "${AUTOLOCK_SECONDS}" "${SIGNING_KEYCHAIN}"
    add_to_search_list
    security unlock-keychain "${SIGNING_KEYCHAIN}"
}

# codesign may use the key without a prompt - only while the keychain is unlocked.
allow_codesign() {
    echo "--- Passwort des Signier-Schluesselbunds / signing keychain password:"
    read -rs KC_PASS
    security set-key-partition-list -S apple-tool:,apple:,codesign: \
        -s -k "${KC_PASS}" "${SIGNING_KEYCHAIN}" >/dev/null 2>&1 \
        || echo "    [!] Fehlgeschlagen - codesign fragt ggf. nach / failed, codesign may prompt"
    unset KC_PASS
}

leaf_sha1() {
    security find-certificate -c "${CERT_NAME}" -Z "${SIGNING_KEYCHAIN}" 2>/dev/null \
        | awk '/SHA-1 hash:/ { print toupper($3); exit }'
}

delete_identities_in() {
    local keychain=$1 sha1
    while security find-certificate -c "${CERT_NAME}" "${keychain}" >/dev/null 2>&1; do
        sha1=$(security find-certificate -c "${CERT_NAME}" -Z "${keychain}" \
            | awk '/SHA-1 hash:/ { print $3; exit }')
        [[ -z "${sha1}" ]] && break
        security delete-identity -Z "${sha1}" "${keychain}" >/dev/null 2>&1 \
            || security delete-certificate -Z "${sha1}" "${keychain}" >/dev/null 2>&1 \
            || break
        echo "    ${sha1} entfernt / removed ($(basename "${keychain}"))"
    done
}

finish() {
    security lock-keychain "${SIGNING_KEYCHAIN}" 2>/dev/null || true
    echo
    echo "Leaf-Zertifikat SHA-1 / leaf certificate SHA-1: $(leaf_sha1)"
    echo "    Notieren - verify-signature.sh vergleicht damit."
    echo "    Write it down - verify-signature.sh compares against it."
    echo "    Optional fest einbauen / optionally hard-code: make CERT_SHA1=$(leaf_sha1)"
}

# ----------------------------------------------------------------- Export --

if [[ "${MODE}" == "export" ]]; then
    [[ -f "${SIGNING_KEYCHAIN}" ]] || { echo "[!] ${SIGNING_KEYCHAIN} fehlt / missing"; exit 1; }
    security unlock-keychain "${SIGNING_KEYCHAIN}"
    echo "--- Export (Passphrase fuer die .p12 waehlen / choose a passphrase for the .p12)"
    security export -k "${SIGNING_KEYCHAIN}" -t identities -f pkcs12 -o "${P12_FILE}"
    chmod 600 "${P12_FILE}"
    echo
    echo "Exportiert nach / exported to: ${P12_FILE}"
    echo "Auf ein externes Medium verschieben / move it to external storage."
    read -r -p "Schluesselbund jetzt von diesem Mac entfernen? / Remove the keychain from this Mac now? [y/N] " answer
    if [[ "${answer}" =~ ^[YyJj]$ ]]; then
        security delete-keychain "${SIGNING_KEYCHAIN}"
        echo "Entfernt / removed. Fuer den naechsten Build / for the next build: $0 --import <file>"
    fi
    exit 0
fi

# ----------------------------------------------------------------- Import --

if [[ "${MODE}" == "import" ]]; then
    [[ -f "${P12_FILE}" ]] || { echo "[!] ${P12_FILE} fehlt / missing"; exit 1; }
    ensure_signing_keychain
    security import "${P12_FILE}" -k "${SIGNING_KEYCHAIN}" -f pkcs12 -T /usr/bin/codesign
    allow_codesign
    finish
    exit 0
fi

# ----------------------------------------------------------------- Create --

if ! command -v openssl >/dev/null 2>&1; then
    echo "[!] openssl nicht gefunden / openssl not found"; exit 1
fi

in_login=0;   security find-certificate -c "${CERT_NAME}" "${LOGIN_KEYCHAIN}"   >/dev/null 2>&1 && in_login=1
in_signing=0; [[ -f "${SIGNING_KEYCHAIN}" ]] && security find-certificate -c "${CERT_NAME}" "${SIGNING_KEYCHAIN}" >/dev/null 2>&1 && in_signing=1
in_system=0;  security find-certificate -c "${CERT_NAME}" "${SYSTEM_KEYCHAIN}"  >/dev/null 2>&1 && in_system=1

if [[ "${FORCE}" -eq 0 ]]; then
    if [[ "${in_login}" -eq 1 || "${in_system}" -eq 1 ]]; then
        echo "[!] \"${CERT_NAME}\" liegt im Anmelde- oder System-Schluesselbund."
        echo "    Jeder Prozess deines Benutzers koennte damit Code signieren, dem das Helper Tool vertraut."
        echo "    Mit --force entfernen und im eigenen Schluesselbund neu erstellen"
        echo "    (danach App UND Helper Tool neu bauen/signieren)."
        echo "[!] \"${CERT_NAME}\" is in the login or system keychain."
        echo "    Any process running as your user could sign code the helper tool trusts."
        echo "    Use --force to remove it and create a new one in the dedicated keychain"
        echo "    (then rebuild/re-sign BOTH the app and the helper tool)."
        exit 1
    fi
    if [[ "${in_signing}" -eq 1 ]]; then
        echo "Zertifikat vorhanden / certificate already exists. Nichts zu tun / nothing to do."
        echo "SHA-1: $(leaf_sha1)"
        exit 0
    fi
else
    echo "--- Entferne alte Zertifikate / removing old certificates ---"
    delete_identities_in "${LOGIN_KEYCHAIN}"
    [[ -f "${SIGNING_KEYCHAIN}" ]] && { security unlock-keychain "${SIGNING_KEYCHAIN}"; delete_identities_in "${SIGNING_KEYCHAIN}"; }
    if [[ "${in_system}" -eq 1 ]]; then
        echo "    System-Schluesselbund (sudo) / system keychain (sudo)"
        sudo security delete-certificate -c "${CERT_NAME}" "${SYSTEM_KEYCHAIN}" >/dev/null 2>&1 || true
    fi
fi

WORKDIR=$(mktemp -d)
trap 'rm -rf "${WORKDIR}"' EXIT
P12_PASS=$(openssl rand -hex 24)

echo "--- Erzeuge Zertifikat / creating certificate: ${CERT_NAME} (${VALID_DAYS} days)"

cat > "${WORKDIR}/cert.cnf" << CNF
[ req ]
distinguished_name = dn
x509_extensions    = v3
prompt             = no

[ dn ]
CN = ${CERT_NAME}

[ v3 ]
basicConstraints     = critical,CA:true
keyUsage             = critical,digitalSignature
extendedKeyUsage     = critical,codeSigning
subjectKeyIdentifier = hash
CNF

openssl req -x509 -newkey rsa:2048 -nodes -days "${VALID_DAYS}" \
    -config "${WORKDIR}/cert.cnf" -keyout "${WORKDIR}/key.pem" -out "${WORKDIR}/cert.pem" 2>/dev/null

# macOS only imports legacy PKCS#12 (3DES + SHA-1); OpenSSL 3 defaults to AES-256.
openssl pkcs12 -export -inkey "${WORKDIR}/key.pem" -in "${WORKDIR}/cert.pem" -name "${CERT_NAME}" \
    -out "${WORKDIR}/bundle.p12" -keypbe PBE-SHA1-3DES -certpbe PBE-SHA1-3DES -macalg sha1 \
    -passout "pass:${P12_PASS}" 2>/dev/null \
  || openssl pkcs12 -export -inkey "${WORKDIR}/key.pem" -in "${WORKDIR}/cert.pem" -name "${CERT_NAME}" \
    -out "${WORKDIR}/bundle.p12" -passout "pass:${P12_PASS}"

ensure_signing_keychain

# Only codesign may use the key - not /usr/bin/security, not any other app.
security import "${WORKDIR}/bundle.p12" -k "${SIGNING_KEYCHAIN}" -P "${P12_PASS}" \
    -T /usr/bin/codesign >/dev/null
rm -f "${WORKDIR}/key.pem" "${WORKDIR}/bundle.p12"

allow_codesign

# Trust in the USER domain only (never System.keychain - that creates a second
# copy and codesign reports "ambiguous").
if security find-identity -v -p codesigning "${SIGNING_KEYCHAIN}" | grep "\"${CERT_NAME}\"" | grep -q CSSMERR \
   || ! security find-identity -v -p codesigning "${SIGNING_KEYCHAIN}" | grep -q "\"${CERT_NAME}\""; then
    echo "--- Setze Vertrauensstellung / setting trust"
    security add-trusted-cert -r trustRoot -p codeSign -k "${SIGNING_KEYCHAIN}" "${WORKDIR}/cert.pem" >/dev/null 2>&1 \
        || echo "    [!] Vertrauensstellung fehlgeschlagen / could not set trust (signing still works)"
fi

security find-identity -v -p codesigning "${SIGNING_KEYCHAIN}" | grep "\"${CERT_NAME}\"" || true
finish
echo
echo "Fertig / done. Jetzt bauen / now build: python3 Build-Project.command"
