# Security Policy

## Supported Versions

Security fixes are applied exclusively to the latest official release. To ensure your system remains secure, always run the most recent release tag.

| Version / Branch | Supported |
| :--- | :--- |
| Latest Tagged Release | :white_check_mark: |
| Pre-release / Draft Tags | Case-by-case |
| Prior / Deprecated Releases | :x: |

Please check the project's [Official Releases Page](../../releases) to confirm you are running the latest version.

---

## SHA256 certificates
Forks should also include in the Release tags the SHA256 certificates of their project, so users can easily verify the SHA256 certificates. Forks should not just copy-paste the SHA256 file from the main project and upload it to Releases.

## Vulnerability disclosure in forks
To ensure **Responsible Vulnerability Disclosure** in forks, forks should enable Private Vulnerability Report via going to Security and quality and enable Private Vulnerability Report. 

## Reporting a Vulnerability

We strongly advocate for **Responsible Vulnerability Disclosure**. If you discover a security flaw in this project, **do not** open a public issue, pull request, or public discussion, unless you're reporting in a fork that has disabled Private Vulnerability Reporting for whatever reason.

### Primary Reporting Method
1. Navigate to the **Security** tab of this repository.
2. Click **Report a vulnerability** to initiate a Private Vulnerability Report.
3. Provide full reproduction steps and a **Safe Proof of Concept (PoC)**. 
   * *A safe PoC must trigger harmless, verifiable behavior (such as writing a benign text log or creating a temporary desktop file) without deploying or executing malicious payloads.*

### Fallback Report Method
If you want to report a vulnerability in a fork that has disabled Private Vulnerability Reporting - open a Bug Report instead, under Mac model, select Not applicable, under macOS version, write a dash (-) and under Issue type, select Security vulnerability - inside the fork itself, not in the main project.

---

## Out-of-Scope Reports

This project intentionally relaxes specific OS security controls to enable extended hardware compatibility. The following items are fundamental to the project's design and are strictly **out-of-scope**:

* **System Integrity Protection (SIP) Modifications:** Disabling or lowering SIP configurations necessary to inject custom kernel extensions.
* **Apple Mobile File Integrity (AMFI) Relaxations:** Disabling AMFI checks required to execute patched runtime drivers.
* **Boot Security & Library Validation Overrides:** System policy adjustments required to bypass native platform hardware restrictions.
* **Exception: if an attacker could trick into injecting policies for T2 Macs on non-T2 systems to intentionally lower security or vice versa, then this is not out out of scope and should be reported immediately.**

Reports concerning these deliberate bypasses will be closed as **By Design**.

---

## Authenticity & Verification Advisory
Forks should not typosquat their names (e.g. BridgеPatcher with a Cyrillic е). The project's former names, OpenCore Legacy Patcher T2 / OpenCore-Legacy-Patcher-T2 / OCLP-T2, also count as typosquats now: they imitate both Dortania's OpenCore Legacy Patcher and this project. If users detect such forks, they should report immediately to the maintainer via Issues. This is an example of typosquatting:
<img width="1712" height="910" alt="image" src="https://github.com/user-attachments/assets/cce043f6-90bd-478a-a4dd-edab048cb79e" />

Also, forks shouldn't spoof their version as if it were newer than it is without disclosing that their project is not affiliated with Albert Müller or its contributors. This is an example of version spoofing:
<img width="1712" height="910" alt="image" src="https://github.com/user-attachments/assets/f114a2e9-a41a-40b6-a8dc-aad2a20bb1d4" />

### Verifying Official Builds
To protect yourself against homoglyph attacks (e.g., lookalike repository names using Cyrillic/Unicode characters) and fake version tagging, always verify binary integrity:

1. Obtain builds **only** from official repository tags.
2. Verify downloaded release archives against the published **SHA-256 checksums**:
   shasum -a 256 <Downloaded-File>.zip

This is an example of typosquatting:
<img width="1712" height="910" alt="image" src="https://github.com/user-attachments/assets/cce043f6-90bd-478a-a4dd-edab048cb79e" />
