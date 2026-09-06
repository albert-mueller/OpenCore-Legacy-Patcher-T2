<div align="center">
  <img src="https://github.com/dortania/OpenCore-Legacy-Patcher/blob/macos-next/docs/images/OC-Patcher.png" alt="OpenCore Patcher Logo" width="200" />
  <h1>OpenCore Legacy Patcher — T1 & T2 macOS Tahoe Edition</h1>
  <p><b>Developed & Maintained by <a href="https://github.com/Medelcartelinc">Medelcartelinc (Matteo)</a> with community contributions</b></p>
  <p><i>Restoring full graphics acceleration, Broadcom Wi-Fi, audio routing, and T1/T2 security on macOS 26 Tahoe & macOS 15 Sequoia</i></p>
</div>

---

### 👑 Authorship & Contributions (Medelcartelinc Fork)
This repository is the dedicated development fork led by **Medelcartelinc (Matteo)**. While building upon work by Dortania, Acidanthera, and Albert Müller, this fork independently engineered the critical solutions that make macOS 26 Tahoe fully usable on legacy and T1/T2 hardware:

1. **T1 Security & Native Login**: Engineered the Native Software Keystore login flow on macOS Tahoe for `MacBookPro14,1`, `MacBookPro14,2`, and `MacBookPro14,3` (retaining Apple ID/iCloud, resolving Keychain panics).
2. **Broadcom Wi-Fi Restoration**: Unblocked the `IOSkywalkFamily` kernel stack in the EFI builder and root patcher on macOS 15 & 26 Tahoe, restoring full Wi-Fi functionality on Broadcom chipsets (`14E4:43BA`).
3. **GPU Hardware Acceleration & GuC**: Injected Intel GuC firmware loading (`igfxfw=2`) and display port keepalive (`igfxonln=1`) for Kaby Lake, eliminating Tahoe UI micro-stutters.
4. **AMD Polaris Power-Gating**: Developed the `radpg=15` and `agdpmod=pikera` patch combination for Radeon Pro 555/560 dGPUs, eliminating GPU switching lags.
5. **AMD Legacy GCN Fix**: Resolved the missing `AMDOpenCL` import bug in root patch payloads.
6. **Tahoe Metal Libraries**: Ported [MetallibSupportPkg](https://github.com/Medelcartelinc/MetallibSupportPkg) to macOS Tahoe (26.x) with multi-endpoint fallback.

---

A Python-based project revolving around [Acidanthera's OpenCorePkg](https://github.com/acidanthera/OpenCorePkg) and [Lilu](https://github.com/acidanthera/Lilu) for both running and unlocking features in macOS on supported and unsupported Macs.
Security researchers can report vulnerabilities in the app via GitHub Security advisories.

⚠️ Attention! Macs with Intel Core 2 Duos:
- 2010 11 inch and 13 inch MacBook Air
- 2010 MacBook Pro
- 2010 Mac mini
- 2010 MacBook
- 2009 MacBook Pro
- 2009 MacBook Air
- 2009 MacBook
- Mac mini 2009
- Mac Pro 2008
- MacBook Air 2008
- MacBook Pro 2008
- MacBook 2008

are unable to boot into macOS 26 Tahoe at all at this moment due to a known limitation of AAAMouSSE and telemetrap causing kernel panics.

> **⚠️ On T2 Macs only, this patcher disables SIP completely to be able to boot macOS properly** What is SIP? SIP, in short for System Integrity Protection, protects against attackers from tampering with core system files. However, on T2 Macs, SIP also causes thermal throttling and other issues when booting via OpenCorePkg, so it needs to be disabled. This doesn’t apply to non-T2 Macs, such as T1 or non-T Macs.


> **⚠️ Building EFIs on Hackintoshes is unsupported by this patcher!** Building EFIs for Hackintoshes is unsupported by this patcher. I’ll explain clearly why: like Dortania’s OCLP, it generates EFIs for real Macs. They wouldn’t work on Hackintoshes. While OpenCore Legacy Patcher T2 uses OpenCore under the hood, real Macs boot differently macOS from a Hackintosh. Also, a GIGABYTE board with let’s say, i7-8700B may offer the exact same board with different configurations, so predicting what patches are needed is infeasible on something no one does know. Also, on the same board depending on the config you may need different boot arguments. And also, it doesn’t use OpenCore’s version of boot.efi, it uses the one that works on real Macs. Standard Hackintoshes rely on BOOTx64.efi. So calling that OpenCore Legacy Patcher T2 can build EFIs for Hackintoshes just because it uses OpenCore is propaganda - it's true that OpenCore works on Hackintoshes, but that's not true for the patcher itself - it either produces EFIs for Hackintoshes or real Macs, that's it. It can't produce for both. So the release note in 4.0.0.16050 says for itself:
fixes a vulnerability where an attacker or bad Hackintosh user could trick a Hackintosh user into building OpenCore EFIs for real Macs by spoofing the SMBIOS as a Mac computer
>
> **⚠️ Das Erstellen von EFIs auf Hackintoshes wird von diesem Patcher nicht unterstützt!** Das Erstellen von EFIs für Hackintoshes wird von diesem Patcher nicht unterstützt. Ich erkläre Ihnen den Grund: Wie Dortanias OCLP generiert er EFIs für echte Macs. Diese würden auf Hackintoshes nicht funktionieren. Obwohl der OpenCore Legacy Patcher T2 im Hintergrund OpenCore verwendet, booten echte Macs macOS anders als ein Hackintosh. Außerdem kann ein GIGABYTE-Board mit beispielsweise einem i7-8700B in verschiedenen Konfigurationen erhältlich sein. Daher ist es unmöglich vorherzusagen, welche Patches benötigt werden, wenn niemand die genaue Konfiguration kennt. Selbst auf demselben Board können je nach Konfiguration unterschiedliche Boot-Argumente erforderlich sein. Zudem verwendet er nicht die OpenCore-Version von boot.efi, sondern diejenige, die auf echten Macs funktioniert. Standard-Hackintoshes verwenden BOOTx64.efi. Die Behauptung, OpenCore Legacy Patcher T2 könne EFIs für Hackintoshes erstellen, nur weil er OpenCore verwendet, ist Propaganda. Zwar funktioniert OpenCore auf Hackintoshes, aber das stimmt nicht für den Patcher selbst. Er erstellt entweder EFIs für Hackintoshes oder für echte Macs, nicht für beides. Die Versionshinweise zu 4.0.0.16050 belegen dies:
"fixes a vulnerability where an attacker or bad Hackintosh user could trick a Hackintosh user into building OpenCore EFIs for real Macs by spoofing the SMBIOS as a Mac computer" oder auf Deutsch übersetzt - „Behebt eine Sicherheitslücke, durch die ein Angreifer oder ein Hackintosh-Nutzer einen Hackintosh-Nutzer dazu verleiten konnte, OpenCore-EFIs für echte Macs zu erstellen, indem er das SMBIOS als Mac-Computer fälschte.“

> **⚠️⚠️⚠️⚠️⚠️ Warning** No support for macOS 27 Golden Gate and newer versions of macOS because macOS 27 Golden Gate and newer versions are arm64-only, so only for Apple Silicon Macs. So the answer is clear. macOS 26 Tahoe is the last supported macOS version by this project.

> **⚠️⚠️⚠️⚠️⚠️ Warnung** macOS 27 Golden Gate und neuere macOS-Versionen sind nicht unterstützt, weil diese sind nur für Apple Silicon/arm64 Macs. Also die Antwort ist klar. macOS 26 Tahoe ist die letzte Version, die von dieser Projekt unterstützt wird.

> **⚠️ EXPERIMENTAL FORK** — Adds **macOS 15 Sequoia and macOS 26 Tahoe support for T2 Macs**. T2 Macs as of now are unsupported by the official OpenCore Legacy Patcher from Dortania. Use it at your own risk. It's still in alpha stage, so I highly recommend to backup all your data and do it only on a spare T2 Mac to experiment. This is experimental alpha software.
## T2 Mac Support

> **💡 Recommended Download**: Always download pre-compiled releases directly from [Releases](https://github.com/Medelcartelinc/OpenCore-Legacy-Patcher-T2/releases) for guaranteed stability, bundled payloads, and proper packaging.

> **🚧 Not ready for general use**

> **Progress:**

- [X] Installer boots
- [ ] MacBookAir8,1 and MacBookAir8,2 can boot the installer
- [ ] Internal hard drive mounts properly on T2 Macs - https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/issues/69
- [ ] ability to reach the desktop
- [ ] Post install - issues with second stage
- [ ] GPU accelaration/WiFi - most T2 Macs will have GPU accelaration out of the box, and on certain T2 Macs, also WiFi

Our goal of this project is to add support for T2 Macs so unsupported T2 Macs can boot into Tahoe. This project does run also on non-T2 Macs and we're committing to improve macOS 26 Tahoe compatability even on non-T2 Macs.

### 🌟 Validated & Working Models on macOS 26 Tahoe (Standard / Safe Build)

Thanks to recent testing and optimizations, the following models are fully operational on macOS 26 Tahoe using the **Standard Build Profile**:

* **MacBook Pro (13-inch, 2017, Two TB3 Ports) — `MacBookPro14,1`**:
  * **Graphics**: Full hardware acceleration via Intel Iris Plus Graphics 640 (Kaby Lake GuC firmware `igfxfw=2`, zero stuttering).
  * **Networking**: Broadcom Wi-Fi (`14E4:43BA` / AirportBrcmNIC) working via Modern Wireless root patching and unblocked Skywalk driver.
  * **Audio & Video**: Fully functional, smooth UI rendering and media playback.
* **MacBook Pro (15-inch, 2017) — `MacBookPro14,3`**:
  * **Graphics**: Dual-GPU switching (Intel HD 630 + AMD Radeon Pro 555/560 with `agdpmod=pikera` & `radpg=15` power-gating fix).
  * **Security & Auth**: T1 security chip supported with password login, Apple Account & iCloud connectivity.
  * **Networking & Audio**: Broadcom Wi-Fi & AppleHDA / AppleALC audio (`alcid=13`).
* **MacBook Pro (13-inch, 2017, Four TB3 Ports) — `MacBookPro14,2`**:
  * Intel Iris Plus 650 graphics acceleration, T1 chip support, Broadcom Wi-Fi and audio.

### 🧪 Work in Progress / Experimental Testing
* **MacBook Pro (15-inch, Mid 2015) — `MacBookPro11,4 / MacBookPro11,5` (Haswell/Broadwell)**:
  * Haswell graphics and Wi-Fi drivers are currently in active testing/development. Do not consider fully validated yet.

---

### ❓ Frequently Asked Questions (FAQ) & Community Feedback

Here are solutions and verified reports from our community threads:

#### Q: Does macOS 26 Tahoe work smoothly on MacBook Pro 2017 models?
* **Yes!** Both `MacBookPro14,1` (13" Function Keys) and `MacBookPro14,3` (15" Touch Bar & T1) are confirmed fully functional on macOS 26 Tahoe with full hardware graphics acceleration, audio, and Broadcom Wi-Fi.
* **Community Report**: See [@azeeproject's test report in Discussion #281](https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/discussions/281#discussioncomment-18310692) and the [Wi-Fi fix confirmation in #18311204](https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/discussions/281#discussioncomment-18311204).

#### Q: How is the Dual GPU and T1 chip handled on MacBook Pro 15" 2017 (`MacBookPro14,3`)?
* **Dual GPU**: Automatic GPU switching works smoothly between Intel HD 630 and AMD Radeon Pro 555/560 using `agdpmod=pikera` and the `radpg=15` power-gating fix to prevent stuttering.
* **T1 Chip & Security**: Retains native password login and Apple Account / iCloud syncing. Headphone jack and speakers work with `alcid=13`.

#### Q: I got `NameError: name 'AMDOpenCL' is not defined` during Root Patching. How do I fix it?
* **Resolution**: This bug in legacy AMD GCN root patching was resolved in release `4.0.0.18002.8` and newer.
* **Community Reference**: See discussion with [@WhiteLighter78 in Issue #194](https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/issues/194#issuecomment-5555940904).

#### Q: Are MacBook Pro 2016 Touch Bar & T1 models (`MacBookPro13,2` / `MacBookPro13,3`) supported on Tahoe?
* **Resolution**: Yes, Touch Bar and keyboard/trackpad control operate natively; login authentication is handled via the Native Software Keystore pipeline.
* **Community Reference**: See discussion with [@TheRaddish1313 in Issue #284](https://github.com/albert-mueller/OpenCore-Legacy-Patcher-T2/issues/284#issuecomment-5556099892).

#### Q: Why did Wi-Fi not turn on after updating to Tahoe?
* **Resolution**: Ensure you are running **4.0.0.18002.9** or newer. Rebuild and install OpenCore EFI to your disk, reboot, and run the **Post-Install Root Patch** ("Networking: Modern Wireless"). The new release unblocks `IOSkywalkFamily` drivers on macOS 15/26.

---

Noteworthy features of OpenCore Legacy Patcher:

* Support for macOS Monterey, Ventura, Sonoma, Sequoia and eventually add support for Tahoe.
* Native Over the Air (OTA) System Updates
* Supports Penryn and newer Macs
* Full support for WPA Wi-Fi and Personal Hotspot on BCM943224 and newer wireless chipsets
* System Integrity Protection, FileVault 2, .im4m Secure Boot and Vaulting
* Recovery OS, Safe Mode and Single-user Mode booting on non-native OSes
* Unlocks features such as Sidecar and AirPlay to Mac even on native Macs
* Enables enhanced SATA and NVMe power management on non-Apple storage devices
* Zero firmware patching required (ie. APFS ROM patching)
* Graphics acceleration for both Metal and non-Metal GPUs

----------

Note: Only clean-installs and upgrades are supported. macOS Big Sur installs already patched with other patchers, such as [Patched Sur](https://github.com/BenSova/Patched-Sur) or [bigmac](https://github.com/StarPlayrX/bigmac), cannot be used due to broken file integrity with APFS snapshots and SIP. Here's an exception: if you are already using patchers like OCLP-Mod or OCLP-Plus or the official OpenCore Legacy Patcher by Dortania, you can revert the root patches and upgrade to this patcher. 

* You can, however, reinstall macOS with this patcher and retain your original data

Note 2: Currently, OpenCore Legacy Patcher officially supports patching to run macOS Monterey through Tahoe installs. For older OSes, OpenCore may function; however, support is currently not provided from Albert Müller.

* For macOS Mojave and Catalina support, we recommend the use of [dosdude1's patchers](http://dosdude1.com)

## Getting Started

To start using the project, please see our in-depth guide:

* [OpenCore Legacy Patcher Guide](https://dortania.github.io/OpenCore-Legacy-Patcher/)

## Support

This project is offered on an AS-IS basis, we do not guarantee support for any issues that may arise. However, there is a community server with other passionate users and developers that can aid you:

* [OpenCore Patcher Paradise Discord Server](https://discord.gg/rqdPgH8xSN)
  * Keep in mind that the Discord server is maintained by the community, so we ask everyone to be respectful.
  * Please review our docs on [how to debug with OpenCore](https://dortania.github.io/OpenCore-Legacy-Patcher/DEBUG.html) to gather important information to help others with troubleshooting.

## Running from source

To run the project from source, see here: [Build and run from source](./SOURCE.md)

## Credits

* [Acidanthera](https://github.com/Acidanthera)
  * OpenCorePkg, as well as many of the core kexts and tools
* [DhinakG](https://github.com/DhinakG)
  * Main co-author
* [Khronokernel](https://github.com/Khronokernel)
  * Main co-author
  * Great amounts of help with debugging, and code suggestions
* [Ausdauersportler](https://github.com/Ausdauersportler)
  * iMacs Metal GPUs Upgrade Patch set and documentation
* [nxvid](https://github.com/nxvid/OpenCore-Legacy-Patcher-T2/)
  * for documenting and fixing an issue where sbvmm might not have been injected on T2 Macs
* [gandolf243](https://github.com/gandolf243)
  * UI redesign
  * fixing some bugs, testing and documenting issues
* [DrDonk](https://github.com/DrDonk)
  * for helping me write a valid patch for AppleKeyStore
  * testing and troubleshooting
* [TheRaddish1313](https://github.com/TheRaddish1313)
    * for fixing framebuffer issues and boot args
    * testing and troubleshooting
* [vit9696](https://github.com/vit9696)
* [Albert Müller](https://github.com/albert-mueller/)
  * Adding support for unsupported T2 Macs and the main author of this fork
  * Help troubleshooting, determining fixes, fixing security vulnerabilities and writing patches
* [YBronst](https://github.com/YBronst/OCLP-Plus)
* for fixing modern wireless on macOS 26 Tahoe
* [pyquick](https://github.com/pyquick) and [hackdoc](https://github.com/hackdoc)
  * [improving support for Metallibs on macOS 26 Tahoe on unsupported non-T2 Macs](https://github.com/hackdoc/OCLP-R)
* [stephandeutsch] (https://github.com/stephandeutsch/OpenCore-Legacy-Patcher/)
  * for fixing USB1.1 compatability with Sequoia and Tahoe
* [vytska69](https://github.com/vytska69)
  * [developing patches for the T2 chip](https://github.com/vytska69/OpenCore-Legacy-Patcher)
  * [Developing Secure Enclave Processor (SEP) timeout patches](https://github.com/vytska69/OpenCore-Legacy-Patcher)
  * [workflow files](https://github.com/vytska69/OpenCore-Legacy-Patcher)
  
  * [peltorio](https://github.com/peltorio/)
    * Fix a critical bug in GitHub Actions by changing macos-version to macos-15
* [kodeaqua](https://github.com/kodeaqua)
  * for research on MacBook Air 2018-2019 hardware to fix boot issues
* [GUTY345](https://github.com/GUTY345)
  * for fixing a bug in OpenCore Legacy Patcher T2 where USB-Map.plist's syntax was invalid and SMBIOS spoofing bug that prevented SMBIOS spoofing from working properly on T2 Macs
  * [fix graphics accelaration on Intel UHD Graphics 630 on unsupported T2 Macs](https://github.com/GUTY345/OpenCore-Legacy-patcher-t2chip-fixBugs/tree/main)
  * [fix your Mac is not supported by macOS 26 Tahoe](https://github.com/GUTY345/OpenCore-Legacy-patcher-t2chip-fixBugs/tree/main)
  * [fix Unsupported Mantissa speed kernel panics on T2 MacBooks](https://github.com/GUTY345/OpenCore-Legacy-patcher-t2chip-fixBugs/tree/main)
* [EduCovas](https://github.com/covasedu)
  * [non-Metal patch set](https://github.com/moraea/non-metal-frameworks) for nVidia Tesla/Fermi/Maxwell/Pascal, AMD TeraScale 1/2, and Intel Core 1st/2nd Generation GPUs
  * [3802 Metal patch set](https://github.com/moraea/misc-patches/tree/main/3802-Metal-15) and [MetallibSupportPkg](https://github.com/dortania/MetallibSupportPkg) for nVidia Kepler and Intel Core 3rd/4th Generation GPUs
  * Metal bundle patches and shims for [nVidia Kepler](https://github.com/moraea/misc-patches/tree/main/Kepler%2013%2B), [AMD GCN 1 - 4](https://github.com/moraea/misc-patches/tree/main/GCN%2013%2B), and [AMD GCN 5 (Vega)](https://github.com/moraea/misc-patches/tree/main/vega%2013%2B)
  * [IOSurface offset patches](https://github.com/moraea/misc-patches/tree/main/Sonoma%2014.4%20IOSurface) for nVidia Kepler, AMD GCN 1 - 5, and Intel Core 3rd - 6th Generation GPUs
  * [legacy Wi-Fi patch set](https://github.com/moraea/unsupported-wifi-patches) restores functionality for Wi-Fi cards in all 2007 - 2017 models
  * [T1 patch set](https://github.com/moraea/misc-patches/tree/main/T1-Patch) restores Touch ID, Apple Pay, and other secure functionality in 2016 - 2017 models
  * AppleGVA downgrade for accelerated video decoding on 2012 - 2016 models
  * OpenCL and OpenGL downgrade for AMD GCN
  * [USB 1 patch](https://github.com/moraea/misc-patches/tree/main/IOUSBHostFamily-14.4)

* [ASentientHedgehog](https://github.com/moosethegoose2213)
  * [non-Metal patch set](https://github.com/moraea/non-metal-frameworks) for nVidia Tesla/Fermi/Maxwell/Pascal, AMD TeraScale 1/2, and Intel Core 1st/2nd Generation GPUs

* [ASentientBot](https://github.com/ASentientBot)
  * [non-Metal patch set](https://github.com/moraea/non-metal-frameworks) for nVidia Tesla/Fermi/Maxwell/Pascal, AMD TeraScale 1/2, and Intel Core 1st/2nd Generation GPUs
  * [Metal bundle interposer](https://github.com/moraea/misc-patches/tree/main/sequoia%2031001%20interposer) for AMD GCN 1 - 5 and Intel Core 5th/6th Generation GPUs
  * [dsce](https://github.com/moraea/dsce) and [shared code](https://github.com/moraea/moraea-common) used by some other patches
* [cdf](https://github.com/cdf)
  * Mac Pro on OpenCore Patch set and documentation
  * [Innie](https://github.com/cdf/Innie) and [NightShiftEnabler](https://github.com/cdf/NightShiftEnabler)
* [Syncretic](https://forums.macrumors.com/members/syncretic.1173816/)
  * [AAAMouSSE](https://forums.macrumors.com/threads/mp3-1-others-sse-4-2-emulation-to-enable-amd-metal-driver.2206682/), [telemetrap](https://forums.macrumors.com/threads/mp3-1-others-sse-4-2-emulation-to-enable-amd-metal-driver.2206682/post-28447707) and [SurPlus](https://github.com/reenigneorcim/SurPlus)
* [dosdude1](https://github.com/dosdude1)
  * Main author of the [original GUI](https://github.com/dortania/OCLP-GUI)
  * Development of previous patchers, laying out much of what needs to be patched
* [parrotgeek1](https://github.com/parrotgeek1)
  * [VMM Patch Set](https://github.com/dortania/OpenCore-Legacy-Patcher/blob/4a8f61a01da72b38a4b2250386cc4b497a31a839/payloads/Config/config.plist#L1222-L1281)
* [BarryKN](https://github.com/BarryKN)
  * Development of previous patchers, laying out much of what needs to be patched
* [mario_bros_tech](https://github.com/mariobrostech) and the rest of the Unsupported Mac Discord
  * Catalyst that started OpenCore Legacy Patcher
* [arter97](https://github.com/arter97/)
  * [SimpleMSR](https://github.com/arter97/SimpleMSR/) to disable firmware throttling in Nehalem+ MacBooks without batteries
* [Mr.Macintosh](https://mrmacintosh.com)
  * Endless hours helping architect and troubleshoot many portions of the project
* [flagers](https://github.com/flagersgit)
  * Aid with Nvidia Web Driver research and development
  * [non-Metal patch set](https://github.com/moraea/non-metal-frameworks) for nVidia Tesla/Fermi/Maxwell/Pascal, AMD TeraScale 1/2, and Intel Core 1st/2nd Generation GPUs
  * [Metal bundle interposer](https://github.com/moraea/misc-patches/tree/main/sequoia%2031001%20interposer) for AMD GCN 1 - 5 and Intel Core 5th/6th Generation GPUs
  * LegacyRVPL, SnapshotIsKill, etc. to aid in rapid testing and development
* [joevt](https://github.com/joevt)
  * [FixPCIeLinkrate](https://github.com/joevt/joevtApps)
* [Jazzzny](https://github.com/Jazzzny)
  * Research and various contributions to the project
  * UEFI Legacy XHCI research and development
  * NVIDIA OpenCL research and development
  * `MacBook5,2` research and development
    * LegacyKeyboardInjector
  * Pre-Ivy Bridge Aquantia Ethernet Patch
  * Non-Metal Photo Booth Patch for Monterey+
  * GUI and Backend Development
    * Updater UI
    * macOS Downloader UI
    * Downloader UI
    * USB Top Case probing
    * Developer root patching
  * Vaulting implementation
  * macOS 15 3802 Helios Research
  * UEFI bootx64.efi research
  * universal2 build research
  * Various documentation contributions
* Amazing users who've graciously donate hardware:
  * [JohnD](https://forums.macrumors.com/members/johnd.53633/) - 2013 Mac Pro
  * [SpiGAndromeda](https://github.com/SpiGAndromeda) - AMD Vega 64
  * [turbomacs](https://github.com/turbomacs) - 2014 5k iMac
  * [vinaypundith](https://forums.macrumors.com/members/vinaypundith.1212357/) - MacBook7,1
   * [ThatStella7922](https://github.com/ThatStella7922) - 2017 13" MacBook Pro (A1708)
  * zephar - 2008 Mac Pro
  * jazo97 - 2011 15" MacBook Pro
  * And others (reach out if we forgot you!)
* MacRumors and Unsupported Mac Communities
  * Endless testing and reporting issues
* Apple
  * for macOS and many of the kexts, frameworks and other binaries we reimplemented into newer OSes
