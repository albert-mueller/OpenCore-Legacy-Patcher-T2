#!/bin/bash
#
# create-signing-certificate.sh
#
# Erstellt ein selbstsigniertes Code-Signing-Zertifikat fuer den lokalen Build
# von OpenCore-Patcher-T2 und importiert es in den Anmeldeschlussel.
#
# Creates a self signed code signing certificate for local builds of
# OpenCore-Patcher-T2 and imports it into the login keychain.
#
# Nutzung / Usage:
#   ./create-signing-certificate.sh
#   ./create-signing-certificate.sh --name "Mein Zertifikat"
#   ./create-signing-certificate.sh --force      # vorhandene ersetzen / replace existing
#

set -euo pipefail

CERT_NAME="OCLP Self Signed"
VALID_DAYS=3650
FORCE=0

LOGIN_KEYCHAIN="${HOME}/Library/Keychains/login.keychain-db"
SYSTEM_KEYCHAIN="/Library/Keychains/System.keychain"

# ---------------------------------------------------------------- Argumente --

while [[ $# -gt 0 ]]; do
    case "$1" in
        --name)
            CERT_NAME="${2:-}"
            if [[ -z "${CERT_NAME}" ]]; then
                echo "[!] --name benoetigt einen Wert / --name requires a value" >&2
                exit 1
            fi
            shift 2
            ;;
        --days)
            VALID_DAYS="${2:-}"
            shift 2
            ;;
        --force)
            FORCE=1
            shift
            ;;
        -h|--help)
            sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "[!] Unbekannte Option / unknown option: $1" >&2
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------ Vorbedingungen --

if [[ "$(uname)" != "Darwin" ]]; then
    echo "[!] Dieses Skript laeuft nur unter macOS."
    echo "[!] This script only runs on macOS."
    exit 1
fi

if ! command -v openssl >/dev/null 2>&1; then
    echo "[!] openssl wurde nicht gefunden / openssl not found"
    exit 1
fi

# ------------------------------------------------- Vorhandene Identitaeten --

existing_count=$(security find-identity -v -p codesigning 2>/dev/null \
    | grep -c "\"${CERT_NAME}\"" || true)

if [[ "${existing_count}" -gt 0 ]]; then
    echo "--- Vorhandene Zertifikate gefunden / existing certificates found ---"
    echo "    ${existing_count}x \"${CERT_NAME}\""
    echo

    if [[ "${FORCE}" -eq 0 ]]; then
        if [[ "${existing_count}" -eq 1 ]]; then
            echo "Ein gueltiges Zertifikat ist bereits vorhanden. Nichts zu tun."
            echo "A valid certificate already exists. Nothing to do."
            echo
            echo "Zum Ersetzen / to replace it: $0 --force"
            exit 0
        fi

        echo "[!] Mehrere Zertifikate mit gleichem Namen - codesign kann sie nicht"
        echo "    unterscheiden (\"ambiguous\"). Mit --force werden alle entfernt"
        echo "    und genau eines neu erstellt."
        echo "[!] Several certificates share this name - codesign cannot tell them"
        echo "    apart (\"ambiguous\"). Use --force to remove them all and create"
        echo "    exactly one replacement."
        exit 1
    fi

    echo "--- Entferne alte Zertifikate / removing old certificates ---"
    while security find-certificate -c "${CERT_NAME}" "${LOGIN_KEYCHAIN}" \
            >/dev/null 2>&1; do
        sha1=$(security find-certificate -c "${CERT_NAME}" -Z "${LOGIN_KEYCHAIN}" \
            | awk '/SHA-1 hash:/ { print $3; exit }')
        [[ -z "${sha1}" ]] && break
        security delete-identity -Z "${sha1}" "${LOGIN_KEYCHAIN}" >/dev/null 2>&1 \
            || security delete-certificate -Z "${sha1}" "${LOGIN_KEYCHAIN}" \
                >/dev/null 2>&1 \
            || break
        echo "    ${sha1} entfernt / removed (login)"
    done

    # Kopien im System-Schluesselbund ebenfalls entfernen - sie fuehren sonst
    # zu "ambiguous". / Also remove copies from the system keychain, they cause
    # the same "ambiguous" error otherwise.
    if security find-certificate -c "${CERT_NAME}" "${SYSTEM_KEYCHAIN}" \
            >/dev/null 2>&1; then
        echo "    Entferne Kopien im System-Schluesselbund (sudo)"
        echo "    Removing copies from the system keychain (sudo)"
        sudo security delete-certificate -c "${CERT_NAME}" "${SYSTEM_KEYCHAIN}" \
            >/dev/null 2>&1 || true
    fi
    echo
fi

# --------------------------------------------------------- Arbeitsverzeichnis --

WORKDIR=$(mktemp -d)
cleanup() {
    rm -rf "${WORKDIR}"
}
trap cleanup EXIT

# Zufaelliges Passwort - die .p12 existiert nur fuer Sekunden.
# Random password - the .p12 only exists for a few seconds.
P12_PASS=$(openssl rand -hex 24)

# ---------------------------------------------- Zertifikat erzeugen / create --

echo "--- Erzeuge Zertifikat / creating certificate ---"
echo "    Name:          ${CERT_NAME}"
echo "    Gueltig / valid: ${VALID_DAYS} Tage / days"

cat > "${WORKDIR}/cert.cnf" << EOF
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
EOF

openssl req -x509 -newkey rsa:2048 -nodes -days "${VALID_DAYS}" \
    -config "${WORKDIR}/cert.cnf" \
    -keyout "${WORKDIR}/key.pem" \
    -out "${WORKDIR}/cert.pem" 2>/dev/null

# macOS (SecKeychainItemImport) versteht nur die alten PKCS#12-Algorithmen.
# OpenSSL 3 nutzt standardmaessig AES-256 + SHA-256 und der Import scheitert
# mit "MAC verification failed". Deshalb 3DES + SHA-1 erzwingen.
#
# macOS (SecKeychainItemImport) only understands the legacy PKCS#12 algorithms.
# OpenSSL 3 defaults to AES-256 + SHA-256, which makes the import fail with
# "MAC verification failed". Force 3DES + SHA-1 instead.
openssl pkcs12 -export \
    -inkey "${WORKDIR}/key.pem" \
    -in "${WORKDIR}/cert.pem" \
    -name "${CERT_NAME}" \
    -out "${WORKDIR}/bundle.p12" \
    -keypbe PBE-SHA1-3DES \
    -certpbe PBE-SHA1-3DES \
    -macalg sha1 \
    -passout "pass:${P12_PASS}" 2>/dev/null \
  || openssl pkcs12 -export \
    -inkey "${WORKDIR}/key.pem" \
    -in "${WORKDIR}/cert.pem" \
    -name "${CERT_NAME}" \
    -out "${WORKDIR}/bundle.p12" \
    -passout "pass:${P12_PASS}"

# ------------------------------------------------------- Import / Vertrauen --

echo "--- Importiere in Anmeldeschluessel / importing into login keychain ---"

import_p12() {
    security import "${WORKDIR}/bundle.p12" \
        -k "${LOGIN_KEYCHAIN}" \
        -P "${P12_PASS}" \
        -T /usr/bin/codesign \
        -T /usr/bin/security >/dev/null 2>&1
}

# Fallback: Schluessel und Zertifikat einzeln als PEM importieren.
# Fallback: import key and certificate separately as PEM.
import_pem() {
    security import "${WORKDIR}/key.pem" \
        -k "${LOGIN_KEYCHAIN}" \
        -t priv -f openssl \
        -T /usr/bin/codesign \
        -T /usr/bin/security >/dev/null 2>&1 \
    && security import "${WORKDIR}/cert.pem" \
        -k "${LOGIN_KEYCHAIN}" \
        -t cert -f openssl \
        -T /usr/bin/codesign \
        -T /usr/bin/security >/dev/null 2>&1
}

if ! import_p12; then
    echo "    PKCS#12-Import fehlgeschlagen, versuche PEM-Import."
    echo "    PKCS#12 import failed, trying PEM import."
    if ! import_pem; then
        echo "[!] Import fehlgeschlagen / import failed"
        exit 1
    fi
fi

echo "--- Erlaube Zugriff ohne Rueckfrage / allowing access without prompts ---"
echo "    Anmeldepasswort / login password:"
read -rs LOGIN_PASS
security set-key-partition-list -S apple-tool:,apple:,codesign: \
    -s -k "${LOGIN_PASS}" "${LOGIN_KEYCHAIN}" >/dev/null 2>&1 \
    || echo "    [!] Fehlgeschlagen - codesign fragt ggf. nach / failed, codesign may prompt"
unset LOGIN_PASS

# ----------------------------------------------------------- Pruefung / check --

identity_lines() {
    security find-identity -v -p codesigning 2>/dev/null \
        | grep "\"${CERT_NAME}\"" || true
}

count_identities() {
    identity_lines | grep -c . || true
}

# Nicht vertrauenswuerdige Zertifikate erscheinen mit einem Zusatz wie
# "(CSSMERR_TP_NOT_TRUSTED)". Build-Project.command hat solche Eintraege
# frueher verworfen, deshalb hier pruefen und Vertrauen setzen.
#
# Certificates that are not trusted show up with a suffix such as
# "(CSSMERR_TP_NOT_TRUSTED)". Build-Project.command used to discard those
# entries, so detect the case and set trust.
count_untrusted() {
    identity_lines | grep -c "CSSMERR" || true
}

echo
echo "--- Pruefe Ergebnis / verifying ---"
found=$(count_identities)

# Nur falls das Zertifikat noch nicht als vertrauenswuerdig gilt, Vertrauen in
# der BENUTZERDOMAENE setzen. Nicht "add-trusted-cert -d ... -k System.keychain"
# verwenden: das legt eine zweite Kopie des Zertifikats im System-Schluesselbund
# an und codesign meldet danach wieder "ambiguous".
#
# Only set trust if the certificate is not considered valid yet, and only in the
# USER domain. Do not use "add-trusted-cert -d ... -k System.keychain": that puts
# a second copy of the certificate into the system keychain, after which codesign
# reports "ambiguous" again.
if [[ "${found}" -eq 0 || "$(count_untrusted)" -gt 0 ]]; then
    echo "    Setze Vertrauensstellung / setting trust"
    security add-trusted-cert -r trustRoot -p codeSign \
        -k "${LOGIN_KEYCHAIN}" "${WORKDIR}/cert.pem" >/dev/null 2>&1 \
        || echo "    [!] Vertrauensstellung fehlgeschlagen / could not set trust"
    found=$(count_identities)
fi

untrusted=$(count_untrusted)

if [[ "${found}" -eq 1 && "${untrusted}" -eq 0 ]]; then
    identity_lines
    echo
    echo "Fertig. Jetzt bauen mit:"
    echo "Done. Now build with:"
    echo "    python3 Build-Project.command"
    exit 0
fi

if [[ "${untrusted}" -gt 0 ]]; then
    identity_lines
    echo
    echo "[!] Das Zertifikat gilt nicht als vertrauenswuerdig."
    echo "    In der Schluesselbundverwaltung: Zertifikat doppelklicken >"
    echo "    Vertrauen > Codesignatur: \"Immer vertrauen\""
    echo "[!] The certificate is not trusted."
    echo "    In Keychain Access: double-click the certificate >"
    echo "    Trust > Code Signing: \"Always Trust\""
    exit 1
fi

echo "[!] Es wurden ${found} Identitaeten gefunden, erwartet war 1."
echo "[!] Found ${found} identities, expected 1."

if [[ "${found}" -gt 1 ]]; then
    echo
    echo "    Betroffene Schluesselbunde / affected keychains:"
    security find-certificate -a -c "${CERT_NAME}" -Z 2>/dev/null \
        | grep -E "keychain:|SHA-1 hash:" | sed 's/^/    /'
    echo
    echo "    Alle entfernen und neu erstellen / remove all and start over:"
    echo "        $0 --force"
fi

exit 1
