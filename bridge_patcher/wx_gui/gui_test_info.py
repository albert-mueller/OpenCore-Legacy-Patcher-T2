"""
gui_test_info.py: Dialog for displaying detailed explanations of all OCLP test levels and patches
"""

import wx
import wx.adv

from .. import constants
from . import gui_support


class TestExplanationDialog(wx.Dialog):
    """
    Dialog providing clear and comprehensive explanations of all build profiles,
    experimental test levels (Level B, Level C, Level D), boot-args, Wi-Fi, Audio, and T1 root patches.
    """

    def __init__(self, parent: wx.Window, global_constants: constants.Constants, initial_tab: int = 0) -> None:
        super().__init__(
            parent,
            title="Guide & Explanation: Test Levels, Wi-Fi, Audio & Patches",
            size=(680, 580),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        self.constants: constants.Constants = global_constants
        self.Centre()

        panel = wx.Panel(self)
        main_sizer = wx.BoxSizer(wx.VERTICAL)

        # Header Title
        title_label = wx.StaticText(panel, label="📘 Comprehensive Guide to Test Levels & Patches")
        title_label.SetFont(gui_support.font_factory(16, wx.FONTWEIGHT_BOLD))
        main_sizer.Add(title_label, 0, wx.ALL | wx.ALIGN_CENTER_HORIZONTAL, 12)

        subtitle_label = wx.StaticText(
            panel,
            label=f"Configuration for {self.constants.custom_model or self.constants.computer.real_model} — macOS Tahoe / T1 Experimental"
        )
        subtitle_label.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
        subtitle_label.SetForegroundColour(wx.Colour(120, 120, 120))
        main_sizer.Add(subtitle_label, 0, wx.BOTTOM | wx.ALIGN_CENTER_HORIZONTAL, 10)

        # Notebook (Tabs) for categories
        notebook = wx.Notebook(panel)

        # --- TAB 1: Livelli di Build EFI ---
        tab_builds = wx.Panel(notebook)
        tab_builds_sizer = wx.BoxSizer(wx.VERTICAL)
        text_builds = wx.TextCtrl(
            tab_builds,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        text_builds.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
        text_builds.SetValue(self._get_builds_text())
        tab_builds_sizer.Add(text_builds, 1, wx.EXPAND | wx.ALL, 8)
        tab_builds.SetSizer(tab_builds_sizer)
        notebook.AddPage(tab_builds, "🧪 Build Levels (A/B/C/D)")

        # --- TAB 2: Boot-args & GPU Dual-Graphics ---
        tab_bootargs = wx.Panel(notebook)
        tab_bootargs_sizer = wx.BoxSizer(wx.VERTICAL)
        text_bootargs = wx.TextCtrl(
            tab_bootargs,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        text_bootargs.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
        text_bootargs.SetValue(self._get_bootargs_text())
        tab_bootargs_sizer.Add(text_bootargs, 1, wx.EXPAND | wx.ALL, 8)
        tab_bootargs.SetSizer(tab_bootargs_sizer)
        notebook.AddPage(tab_bootargs, "🚀 Boot-args & GPU")

        # --- TAB 3: T1 Chip & Password Login ---
        tab_t1 = wx.Panel(notebook)
        tab_t1_sizer = wx.BoxSizer(wx.VERTICAL)
        text_t1 = wx.TextCtrl(
            tab_t1,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        text_t1.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
        text_t1.SetValue(self._get_t1_text())
        tab_t1_sizer.Add(text_t1, 1, wx.EXPAND | wx.ALL, 8)
        tab_t1.SetSizer(tab_t1_sizer)
        notebook.AddPage(tab_t1, "🔐 T1 & Tahoe Login")

        # --- TAB 4: Wi-Fi & Audio (Tahoe Fixes) ---
        tab_wifi_audio = wx.Panel(notebook)
        tab_wifi_audio_sizer = wx.BoxSizer(wx.VERTICAL)
        text_wifi_audio = wx.TextCtrl(
            tab_wifi_audio,
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_RICH2
        )
        text_wifi_audio.SetFont(gui_support.font_factory(11, wx.FONTWEIGHT_NORMAL))
        text_wifi_audio.SetValue(self._get_wifi_audio_text())
        tab_wifi_audio_sizer.Add(text_wifi_audio, 1, wx.EXPAND | wx.ALL, 8)
        tab_wifi_audio.SetSizer(tab_wifi_audio_sizer)
        notebook.AddPage(tab_wifi_audio, "🌐 Wi-Fi & 🔊 Audio (Tahoe)")

        # Select requested tab
        if 0 <= initial_tab < notebook.GetPageCount():
            notebook.SetSelection(initial_tab)

        main_sizer.Add(notebook, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)

        # Bottom Close Button
        button_sizer = wx.BoxSizer(wx.HORIZONTAL)
        btn_close = wx.Button(panel, label="Close", size=(120, 32))
        btn_close.SetFont(gui_support.font_factory(12, wx.FONTWEIGHT_NORMAL))
        btn_close.Bind(wx.EVT_BUTTON, lambda event: self.EndModal(wx.ID_OK))
        button_sizer.Add(btn_close, 0, wx.ALL, 10)
        main_sizer.Add(button_sizer, 0, wx.ALIGN_RIGHT)

        panel.SetSizer(main_sizer)
        panel.Layout()

    def _get_builds_text(self) -> str:
        return """============================================================
OPENCORE BUILD LEVELS (PROFILES)
============================================================

1. 🟢 STANDARD / SAFE BUILD:
   • Purpose: Stock and stable OpenCore configuration certified by Dortania.
   • SMBIOS: Retains the Mac's native SMBIOS.
   • Target: Daily use on supported and validated macOS versions.

------------------------------------------------------------

2. 🧪 [LEVEL-B] EXPERIMENTAL GPU:
   • Purpose: Diagnostics and isolation of graphical (GPU) issues.
   • Kexts: Injects and activates WhateverGreen.kext version 1.7.0.
   • Boot-args: Injects '-wegnoegpu' to disable power/switching
     of the dGPU and isolate testing to the Intel integrated graphics only.

------------------------------------------------------------

3. 🧪 [LEVEL-C] EXPERIMENTAL TAHOE (MacBookPro14,x):
   • Goal: Experimental support for macOS 26 (Tahoe) maintaining
     the native hardware identity of the MacBook Pro 2017 (T1).
   • Automatic Boot-args:
     - 'dart=0': Resolves IOMMU mapping and Wi-Fi/Bluetooth peripheral issues.
     - 'agdpmod=ignore': Bypasses Apple Graphics Device Policy and prevents
       black screens on boot for MacBookPro14,x (Polaris + Kaby Lake).
     - 'cryptex=0 cs_allow_invalid=1': Allows kernel loading.
   • SMBIOS: Native ('MacBookPro14,x'), without spoofing.

------------------------------------------------------------

4. 🧪 [LEVEL-C] EXPERIMENTAL SPOOF T2:
   • Purpose: Spoofs the SMBIOS to 'MacBookPro16,1' / 'MacBookPro16,2' (T2 Mac)
     to verify the behavior of the Tahoe installer and kernel.
   • T2 Bypass: Bypasses the T2 abort block within the OCLP app.
   • Includes all Level C boot-args (dart=0, agdpmod=ignore, cryptex=0).

------------------------------------------------------------

5. 🧪 [LEVEL-D] EXPERIMENTAL ALL-IN-ONE (Wi-Fi + Audio + GPU + T1):
   • Purpose: COMPLETE profile with all patches active simultaneously.
   • Wi-Fi: Activates IOSkywalkFamily, IO80211FamilyLegacy, AirportBrcmFixup,
     blocks the native Skywalk driver, and uses boot-arg 'ipc_control_port_options=0 amfi=0x80'.
   • Audio: Activates AppleALC.kext + 'alcid=13' (HDEF codec for MacBookPro14,x).
   • GPU: Dual-GPU Kaby Lake + Polaris with 'agdpmod=pikera' and 'dart=0'.
   • T1 Security: Secure login with password only + iCloud/Apple ID account support.
"""

    def _get_bootargs_text(self) -> str:
        return """============================================================
SPECIFIC BOOT-ARGS EXPLANATION FOR MACBOOKPRO14,x
============================================================

• dart=0
  Disables IOMMU / VT-d virtualization at the macOS kernel level.
  Fundamental on MacBookPro14,x and Mac 2017 to prevent crashes of the Broadcom Wi-Fi
  driver (14E4:43BA), Bluetooth, and PCIe controllers on macOS Tahoe.

• agdpmod=ignore (or agdpmod=pikera in LEVEL-D)
  Bypasses AppleGraphicsDevicePolicy (AGDP) checks.
  On MacBook Pros with dual GPUs (Intel HD 630 + AMD Polaris), AGDP
  tends to disable video outputs when WindowServer starts,
  causing a black screen. This patch forces the driver to keep
  framebuffer connections active.

• ipc_control_port_options=0
  Disables the restrictive Mach port IPC message checking introduced
  in macOS Tahoe. Essential to allow communication between network daemons
  'wifip2pd' / 'airportd' and the kernel without causing crashes or bootloops.

• amfi=0x80
  Allows patched binaries in the root volume (Wi-Fi, graphics, frameworks)
  to be executed by the kernel without being blocked by Apple Mobile File Integrity.

• alcid=13
  Injects Audio Layout ID 13 for AppleALC.kext, matching the analog codec
  of MacBookPro14,x (Realtek ALC / AppleHDA).

• cryptex=0 & cs_allow_invalid=1
  Disables the Cryptex cryptographic authentication requirement and allows
  the kernel to boot with modified kexts and binaries.

• -lilubetaall
  Forces Lilu and all its plugins (AppleALC, WhateverGreen, AirportBrcmFixup)
  to load even on newer versions of macOS (Tahoe 26.x).
"""

    def _get_t1_text(self) -> str:
        return """============================================================
T1 SECURITY CHIP & AUTHENTICATION ON MACOS TAHOE (26.x)
============================================================

• Why is Touch ID inactive on Tahoe?
  Apple completely removed support for the T1 chip's USB bridge
  in the latest macOS versions. Forcing old kexts and frameworks from
  Ventura (AppleKeyStore 13.6, biometrickitd, SharedUtils 13.6) creates a
  severe ABI/IPC incompatibility with native Tahoe security daemons
  (securityd, LocalAuthentication, akd), blocking password prompts
  in System Settings and Apple Account access.

• How does Native Software Keystore mode work on Tahoe?
  1. Preserves Tahoe's native kernel drivers (AppleKeyStore & AppleCredentialManager)
     running them in software mode (Intel Kaby Lake CPU crypto).
  2. 100% restores PASSWORD authorization in System Settings.
  3. Allows modification and management of account passwords and the keychain.
  4. Unlocks Apple Account / iCloud access thanks to the integration of
     AMFIPass (-amfipassbeta) and attestation bypass (-oas_skip_attestation).
  5. Keeps the Touch Bar (TouchBarServer / AppleHSSPISupport) fully stable.

• Result:
  Reliable local and cloud authentication, working system lock unlock,
  operational Apple Account, with no black screens or SecurityAgent crashes.
"""

    def _get_wifi_audio_text(self) -> str:
        return """============================================================
ANALYSIS & OPERATION OF WI-FI AND AUDIO ON MACOS TAHOE
============================================================

🔊 HOW AUDIO WORKS ON TAHOE (OCLP-MOD & OCLP PLUS):
1. Problem:
   Starting with macOS 26 Tahoe, Apple completely removed 'AppleHDA.kext'
   from the OS, breaking analog audio (internal speakers,
   microphones, and headphone jacks) on Macs without a T2 chip.
2. Integrated Solution:
   • Root Patcher Side: The 'ModernAudio' patchset re-injects 'AppleHDA.kext'
     into /System/Library/Extensions.
   • EFI Side: 'AppleALC.kext' (v1.9.7) is loaded alongside 'Lilu.kext',
     injecting 'alcid=13' and '-lilubetaall'.
   • Result: CoreAudio correctly recognizes the hardware codec and restores
     the integrated audio input and output.

------------------------------------------------------------

🌐 HOW WI-FI WORKS ON TAHOE (BROADCOM BCM943602 / 14E4:43BA):
1. Problem:
   Apple replaced the traditional Wi-Fi subsystem with the new
   Skywalk architecture, dropping native support for Broadcom chipsets.
2. Integrated Solution:
   • In OpenCore EFI (Kernel -> Block): The native driver
     'com.apple.iokit.IOSkywalkFamily' is blocked.
   • In OpenCore EFI (Kernel -> Add): The following are injected:
     - 'IOSkywalkFamily.kext' (v1.2.0)
     - 'IO80211FamilyLegacy.kext' (and plugin 'AirPortBrcmNIC.kext')
     - 'AirportBrcmFixup.kext' (with 'brcmfx-country=IT')
     - 'AMFIPass.kext'
   • Essential Boot-args:
     - 'ipc_control_port_options=0': Prevents Mach IPC port crashes between
       network daemons 'wifip2pd' and the Tahoe kernel.
     - 'amfi=0x80': Allows execution of modified network services.
   • Root Patcher Side: After the first boot, 'Install drivers and patches'
     applies the 'ModernWireless' patchset which merges 'IO80211.framework' and
     'WiFiPeerToPeer.framework' into the root volume.
"""
