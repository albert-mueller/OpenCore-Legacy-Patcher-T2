#!/bin/zsh --no-rcs
# ------------------------------------------------------
# Privileged Helper Tool Installer
# ------------------------------------------------------
# Moves to expected destination and sets SUID bit.
# ------------------------------------------------------
# Developed for internal testing, end users should be
# using the PKG installer when released.
# ------------------------------------------------------


# MARK: Variables
# ---------------------------
helperName="com.albert-mueller.opencore-patcher-t2.privileged-helper"
helperPath="/Library/PrivilegedHelperTools/$helperName"
sourcePath="./$helperName"


# MARK: Main
# ---------------------------

if [[ $EUID -ne 0 ]]; then
    echo "Run with sudo"
    exit 1
fi

if [[ ! -f "$sourcePath" || -L "$sourcePath" ]]; then
    echo "Helper not found (or a symlink): $sourcePath"
    exit 1
fi

# A release build only trusts callers signed with its own leaf certificate,
# so an unsigned or ad-hoc signed helper would refuse every command anyway.
if ! /usr/bin/codesign --verify --strict "$sourcePath" 2>/dev/null; then
    echo "Helper is not validly signed - sign it first (see README.md)"
    exit 1
fi
if ! /usr/bin/codesign -dvv "$sourcePath" 2>&1 | /usr/bin/grep -q "^Authority="; then
    echo "Helper is ad-hoc signed (no certificate) - sign it with your certificate first"
    exit 1
fi

/bin/mkdir -p /Library/PrivilegedHelperTools
/bin/rm -rf "$helperPath"
/bin/cp "$sourcePath" "$helperPath"
/usr/sbin/chown root:wheel "$helperPath"
/bin/chmod 4755 "$helperPath"

echo "Installed: $(/usr/bin/stat -f '%Su:%Sg %Lp' "$helperPath") $helperPath"
