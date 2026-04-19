# OpenCore Legacy Patcher T2 changelog
## 3.0.0 alpha 13:
This version removes the broken WhateverGreen.kext from the code. When there is a new fully functional file, I'll add it again.

## 3.0.0 alpha 14:
This release adds a stable version of WhateverGreen.kext directly from Dortania. But how good it works with iBridged remains to be tested.

## 3.0.0 alpha 12:
This release fixes the following issues:
GUI and Backend Improvements
Fix Build & Install Frame Stability:

Implemented finally blocks in gui_build.py and gui_install_oc.py to ensure logging handlers are properly detached.

This resolves the RuntimeError: C/C++ object has been deleted when transitioning between build and installation screens.

Refactor Thread Management:

Replaced index-based handler removal (handlers[2]) with explicit object references.

Fixes IndexError: list index out of range occurring on faster machines or when disk unmounting is delayed.

Improve Installation Reliability:

Restored missing backend calls in the installation thread to ensure OpenCore is actually written to the EFI partition.

Fixed a logic bug where self.result wasn't being updated, which previously prevented the "Success" and "Reboot" prompts from appearing.

Python 3.13/3.14 Compatibility:

General code cleanup to support stricter object lifecycle management in newer Python environments.

## 3.0.0 alpha 11:
This release:
- Resolved RuntimeError: wrapped C/C++ object of type TextCtrl has been deleted in the Build Frame. This was achieved by implementing a finally block to ensure the ThreadHandler is explicitly removed from the global logger before the UI frame is destroyed, preventing race conditions during build-to-install transitions.

## 3.0.0 alpha 10 and 10S:
3.0.0 alpha 10 alongside 3.0.0 alpha 10S fixes the following issues:
- In updates.py, REPO_LATEST_RELEASE_URL was pointing to a web page. This bug affects all versions from 3.0.0 alpha 2 onwards.
- Fixes a bug in gui_build.py that prevents OpenCore EFIs from building.

Known issue:
- core.py panics as soon as trying to apply OpenCore EFIs and thus the app crashes

## 3.0.0 alpha 9
This release:
- Adds the special version of WhateverGreen that works with iBridged - but will not be injected automatically via OCLP until a future alpha release, just like the iBridged.kext. To inject these, first build the EFI via the OpenCore Legacy Patcher app as you would do noramlly, and then add those 2 kexts via OCAuxiliaryTools or ProperTree.
- Fixes a bug in logging_handler.py that makes the application less stable or outright to crash
- Now, when the OpenCore Legacy Patcher app crashes, it will show the error just like pre-alpha 5, so for example attackers can't unknowingly exploit vulnerabilities, for example - to crash the app and unknowingly to the user they execute malicious code. This bug affects this repository only. It's both a bug and a vulnerability.

To fix this vulnerability, update to the latest version available.

## 3.0.0 alpha 8:
This release will start enabling WhateverGreen.kext for unsupported T2 Macs to allow patching GPUs in the future - but only partially. And this release also fixes a vulnerability where when trying to build OpenCore EFI on unsupported T2 Macs, an attacker can prevent from building the EFI and execute arbitary code in the background unknowingly while to the user it shows an error only. This vulnerability affects this project only. This vulnerability was present since 3.0.0 alpha 1.

To fix this vulnerability, update to the latest version available.

## 3.0.0 alpha 7
This release adds:
- a very experimental version of iBridged to add T2 spoofing capabilities. This will allow booting into macOS 15 Sequoia and macOS 26 Tahoe, but for 26 Tahoe, at the release of iBridged 1.1.0b1 support is incomplete. The kext overall will see improvements in future alpha versions. It may have some bugs still pending to be fixed. The kext will not be automatically injected into OpenCore automatically yet, as it may be not fully stable yet. But for this to work, you need an SMBIOS of a unsupported or supported T2 Mac. On unsupported T2 Macs, you generally may not need SMBIOS spoofing to get it to work.
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
