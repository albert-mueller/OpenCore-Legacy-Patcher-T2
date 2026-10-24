# OpenCore Patcher Terminology

With OpenCore Legacy Patcher T2, we use a lot of different terms to refer to hardware including `SSE4.1`, `32-Bit Firmware`, etc. This page is to help users understand what all these confusing words mean.

# Terminology

Term | Description
--- | ---
**macOS**        | Apple's own UNIX based OS used for Mac machines and is "What makes a Mac a Mac".
**Windows**      | Microsoft's proprietary OS that is used and supported on a wide range of devices
**Linux**        | Family of open source Unix-like operating systems based on the Linux kernel, an operating system kernel first released on September 17, 1991, by Linus Torvalds. Linux is typically packaged in a Linux distribution. Note that while macOS and Linux may be UNIX-based, they're vastly different.
**Bootloader**   | Piece of software that loads an OS, usually made by the OS creators. OpenCore is technically not a bootloader per se (see boot manager explanation down below). Apple's Boot.efi would be the actual boot loader in a Mac.
**Boot Manager** | Piece of software that manages bootloaders – we have many of these: Clover, systemd-boot, OpenCore, rEFInd, rEFIt... These are generally seen as prepping the system for the actual boot loader.
---
Term | Description
--- | ---
**OpenCore**   | A boot manager by the [Acidanthera team](https://github.com/acidanthera). It loads macOS on Macs Apple no longer supports, with faster booting and a lighter footprint than older boot managers. OpenCore itself can keep macOS security features like SIP, FileVault and Apple Secure Boot working. **With OpenCore Legacy Patcher T2, several of these are deliberately weakened:** root patching requires partly disabling SIP and the sealed system volume, AMFI may be disabled, and on T2 Macs Apple Secure Boot is turned off. Your Mac is less protected than a supported Mac running stock macOS, so only use it if you accept that trade-off.
**ACPI**   | Tables defined in your firmware defining your hardware and different methods, tied directly to how IOKit/IOService handles device setup
**NVRAM**   | Non-volatile storage, where many variables are stored including default boot options, Hibernation keys, Secure Boot information, etc
---
Term | Description
--- | ---
**XNU**    | Also known as **X** is **N**ot **U**nix, XNU is referred to as macOS's "kernel" and the heart of what makes macOS tick
**Kexts**   | Also known as **K**ernel **Ext**ensions, are macOS's drivers. They're used to perform different tasks like device drivers or for a different purpose (in this patcher) like patching the OS, injecting information or running tasks.
**KernelCollection**   | Also known as the ImmutableKernel and PrelinkedKernel, this is a bundle of the kernel(XNU) and kernel extensions(Kexts) that we use to boot macOS. This is also what OpenCore patches in memory to allow us to have a seamless experience <br/>- PrelinkedKernel: Default caching system since 10.7 <br/>- ImmutableKernel: Secure Boot based caching system since 10.13 <br/>- KernelCollection: Merge of both Prelinked and ImmutableKernel's since macOS 11, Big Sur
**IOKit**   | Backbone of how Kernel Extensions (Kexts) probe and attach onto hardware, starts quickly after kernel initiates
**WindowServer**   | Backbone of the GUI interface in macOS, one of the first userfacing userspace programs to kick-in
**OTA**   | Short for **O**ver **T**he **A**ir, refers to native OS updates via System Preferences like a supported Mac
**DELTA**   | Often used with OTA, refers to OS updates that much smaller than full installers (generally ~3GB), note Deltas require the root volume to be unmodified otherwise ~12GB updates will occur.
---
Term | Description
--- | ---
**EFI**   | It can denote two things: <br/>- Mac's firmware, which is the same as UEFI, but pretty modified for Macs only, so not so "Universal" <br/>- The partition on your hard drive that stores software read by the UEFI to load OSes (like the Windows bootloader) or UEFI Applications (like OpenCore), it's FAT32 formatted and has an ID type of EF00 (in hex). It can be named ESP or SYSTEM, and it's usually from 100MB to 400MB in size but the size doesn't reflect upon anything.
**HFS+**   | Also known as Mac OS Extended (Journaled), this was the default macOS drive format up until macOS 10.13. It was designed around spinning disks.
**APFS**   | This is the default macOS drive format from macOS 10.13 and onwards for SSDs, and standard for all drives in Mojave. This format was designed primarily around SSDs.
**32 and 64-Bit CPU**   | The bit number of a CPU determines how much data a CPU can address. <br/>- 32-Bit CPUs were only supported up-to Mac OS X 10.6, Snow Leopard.
**32-Bit Firmware**   | The bit number of a Firmware determines how much data the firmware can address. In some older Macs, it's common to have a 64-Bit CPU with a 32-Bit firmware<br/>- 32-Bit Firmwares were only supported up-to Mac OS X 10.7, Lion.
**SSE Instructions**   | Also known as **S**IMD **S**ingle-Precision Floating-Point **I**nstructions,  these are defined as instruction sets supported by your CPU. In macOS, there are a certain number of instruction sets required for normal operation: <br/>- SSE3: Required for all Intel CPUs since Mac OS X 10.4, Tiger <br/>- SSSE3: Required for all Intel 64-Bit CPUs since Mac OS X 10.6, Snow Leopard  <br/>- SSE4.1: Required for all Intel CPUs since macOS 10.12, Sierra
---
