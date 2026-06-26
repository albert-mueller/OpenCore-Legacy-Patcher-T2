# Collecting MacBookAir8,2 boot evidence

Use this only after building OpenCore from the `research/mba8x-tahoe-boot` branch.

## Before booting

- Use a USB installer, not the internal EFI, for the first test.
- Keep the existing working macOS installation untouched.
- Enable OpenCore DEBUG and verbose boot.
- Run `ocvalidate` against the generated `EFI/OC/config.plist`.

## Files to preserve

After the failed or successful test, mount the USB EFI partition and copy:

- `EFI/OC/config.plist`
- every `opencore-*.txt` file from the EFI volume
- the patcher build log, usually `~/Library/Logs/OpenCore-Patcher.log` or `~/OpenCore-Patcher.log`

Also photograph the final verbose line shown on screen. The image must include several lines above the last line, because the final printed message is not always the actual cause.

## Minimum test report

- Host model: `MacBookAir8,2`
- macOS installer version and build number
- Whether the internal SSD appears in Disk Utility
- Whether installation reaches the first reboot
- Exact remaining-time value if it stops
- Whether the machine reboots, freezes, or shows a prohibited symbol

Do not post serial numbers, MLB values, ROM values, or Hardware UUIDs in public issues.
