# OpenCore Legacy Patcher T2 changelog
## 3.0.0 alpha 7
This release adds:
- a very experimental version of iBridged to add T2 spoofing capabilities. This will allow booting into macOS 15 Sequoia and macOS 26 Tahoe, but for 26 Tahoe, at the release of iBridged 1.1.0b1 support is incomplete. The kext overall will see improvements in future alpha versions. It may have some bugs still pending to be fixed. The kext will not be automatically injected into OpenCore automatically yet, as it may be not fully stable yet. But for this to work, you need an SMBIOS of a unsupported or supported T2 Mac.
- All update links are changed from Dortania's original OpenCore Legacy Patcher to this repository, but the update infrastructure is not yet complete

This release also fixes the following vulnerabilities:
- sys.exit at OpenCore-Patcher-GUI.command was set 1 instead of 3. This allows attackers to crash the project to execute arbitary code and take advantage of other vulnerabilities without a human to realize. This vulnerability affects this repository only. Dortania's own is not affected by this.
- Updated follow-redirects dependency to resolve a security vulnerability (CVE-2024-28849). This prevents potential credential leakage during documentation build processes. This affects both this and Dortania's own repository.

To fix these vulnerabilities, update to the latest version available.
## 3.0.0 alpha 6
This release fixes the following:
- To Mac Pro 2019 users they were offered OpenCore EFIs for unsupported Macs, while the 2019 Mac Pro supports Tahoe natively
- On macOS 26 Tahoe Root Patching was greyed out - unblocking this feature allows any unsupported Macs to get root patches on macOS 26 Tahoe. But I have a big warning:  This project is focusing only on T2 Macs for now. On non-T2 Macs, their drivers on some Macs are full of memory corruption bugs, and macOS 26 Tahoe is very strict about this. macOS Tahoe blocks by default known vulnerable kexts by default, much more like Windows 11's Vulnerable Driver Blocklist. On macOS, disabling this is not as simple as in Windows 11 - on Windows 11, it's as easy as going to Windows Defender and disable the option for Vulnerable Driver Blocklist. On macOS, it's not like this. Also, many non-T2 Macs like the 2007-2009 Macs, had received their last update in 2018, which means their kexts are essentially frozen back in time. 

## 3.0.0 alpha 5
- fixes an issue that prevents from building the OpenCore into the disk - the fix is temporary and requires when building the EFI to enter the password inside the Terminal app
- fixes a bug where on T2 Macs it puts inside the EFI 2 Lilus and CryptexFixups.
- Removes requirements for Apple certificates

🛡️ Security & Hardening:
These vulnerabilities affect both this repository and Dortania's official repository.
Resolved Path Injection Vulnerability (CWE-427): Hardened the application entry point by stripping the current working directory from sys.path. This prevents the execution of malicious local scripts during app startup.

Internal Path Sanitization: Implemented generic error handling in the PyInstaller entry point to prevent leaking sensitive local system paths and usernames via Python tracebacks.

Privileged Execution Refactoring: Transitioned from a fixed Privileged Helper Tool binary to a dynamic sudo-based execution model, reducing dependencies on signed external binaries while maintaining system-level task capabilities.

When building the EFI, an attacker could write invalid synthax to crash the project, or worse - execute arbitary code. This is fixed by wrapping with try/except blocks.
## 3.0.0 alpha 4.3
- fixes an issue where OpenCore Legacy Patcher T2 won't open
- fixes an issue that prevents from building the OpenCore into the disk partially
## 3.0.0 alpha 4.2
- fixes a vulnerability where in constants.py the repository to check for updates was https://github.com/p8bpg9zrw7-collab/OpenCore-Legacy-Patcher-T2 - the old link. An attacker could redirect to a malicious GitHub repository or could launch a malicious redirect to install malware, for example AtomicStealer. This vulnerability affects versions from 3.0.0 alpha 2 all the way until 3.0.0 alpha 4.1.
## 3.0.0 alpha 4.1
- Fixed broken files that when uploading to GitHub they broke while uploading. This increases stability of the OpenCore Legacy Patcher T2 app.
- Changed the GitHub repository to a clean repo to clean the mess of broken files.
- Removed the iBridged.kext to clean broken files. I'm planning to readd these soon.

## 3.0.0 alpha 4
- Switch KDK comments and messages from Chinese to English
- Now iBridge's source code is no longer stored in a zip file, so you can read it at any time
## 3.0.0 alpha 3
- This version patches a security vulnerability in the networking library that could have allowed for insecure connections when downloading macOS assets or patches. (Updated requests to 2.32.2). This vulnerability affects both this repository and Dortania's official OpenCore Legacy Patcher repository. To address this vulnerability, update to the latest available release.
## 3.0.0 alpha 2
- Now it will always check for updatees from our repository instead of Dortania's
- Bug fixes in OpenCore Legacy Patcher T2 prevents from flashing the OpenCore bootloader, regardless of the Mac model.
- Add the original source code of iBridged.kext, which requires some work to fix its vulnerabilities.
## 3.0.0 alpha 1
- Add partial support for unsupported T2 Macs

## 3.0.0 (initial release of the official OpenCore Legacy Patcher 3.0.0)
- Restore support for FileVault 2 on macOS 26
- Add USB mappings for macOS 26
- Adopt Liquid Glass-conformant app icon
- Increment Binaries:
  - OpenCorePkg 1.0.5 - rolling (f03819e)
