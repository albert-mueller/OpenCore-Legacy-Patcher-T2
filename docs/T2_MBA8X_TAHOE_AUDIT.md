# T2 MacBookAir8,x Tahoe audit

This branch tracks the MacBookAir8,1 and MacBookAir8,2 boot/install work for macOS Tahoe.

## Confirmed change

The Tahoe SMBIOS route for `MacBookAir8,1`, `MacBookAir8,2`, and `MacBookAir9,1` is now explicit and locked to `MacBookPro16,2`.

This prevents a regression to the incompatible `MacBookPro16,4` / `J215AP` pair discussed in issue #92. A Linux-safe static checker and GitHub Actions workflow enforce the mapping.

## Current blockers found during source audit

1. The T2 patch block in `efi_builder/misc.py` contains a kernel patch whose `Find` and `Replace` byte lengths differ. OpenCore requires both fields to have equal size when `Find` is present. This entry must be converted to an immediate symbol replacement or padded with a verified signature before testing.
2. The disabled experimental T2 block appends a list into `Kernel -> Patch` instead of extending the patch list. Enabling it would create a malformed config structure.
3. Several Tahoe-specific patches use `MinKernel = 24.0.0`, which also targets Sequoia. This must not be changed until the actual Tahoe installer kernel version and boot logs are collected.
4. Kext injection currently logs and returns when a required kext cannot be found. For mandatory T2 dependencies, the build should fail loudly instead of producing an incomplete EFI.

## Safe test order

1. Build an EFI for the real host model `MacBookAir8,2`.
2. Confirm `#Revision -> Spoofed-Model` and `PlatformInfo -> Generic -> SystemProductName` resolve to `MacBookPro16,2`.
3. Run `ocvalidate` before writing the EFI to the USB drive.
4. Boot with OpenCore DEBUG and verbose mode.
5. Preserve the OpenCore log and photograph the final verbose line if the installer stops.
6. Do not test unverified SEP, AppleKeyStore, USB, or corecrypto bypass patches on the internal disk.

## Required evidence for the next patch

- Generated `EFI/OC/config.plist` from this branch.
- `opencore-*.txt` DEBUG log from the EFI volume.
- Photo of the final verbose boot screen.
- Tahoe installer build number and the stage where it stops.
