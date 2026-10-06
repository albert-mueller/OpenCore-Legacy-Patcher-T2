# T2 Stage 2 Firmware Bypass for macOS Tahoe (OCLP)

**Authors:** Matteo ([@Medelcartelinc](https://github.com/Medelcartelinc)) & Contributors  
**Target:** Apple T2 Macs (2018–2019: MacBookPro15,1, MacBookPro15,2, MacBookPro15,3, MacBookPro15,4, Macmini8,1, iMacPro1,1, MacBookAir8,1, MacBookAir8,2)  
**Status:** Tested & Operational (100% verification against `OSInstaller.framework` runtime on macOS)

---

## 1. Problem Overview
During **Stage 2** of the macOS Tahoe installation, `osinstallersetupd` invokes the `OSInstaller.framework` pipeline queue (`OSIInstallQueueElement`).

On unsupported T2 Macs, Apple dropped the signed BridgeOS firmware packages for older Board-IDs (`j680ap`, `j174ap`, `j137ap`, etc.). When the installer reaches:
- `OSIUpdateFirmwareElement`
- `OSIVerifyROMElement`
- `OSIInstallPersonalizedManifestsElement`

The methods query the T2 chip and Apple's personalization servers. Failing to find a matching firmware payload for that Board-ID, the methods return `NO` (`0`) and populate an `NSError`. This immediately halts Stage 2 with an unrecoverable installation failure.

Previous workarounds attempted global `x86-VMM` spoofing, but on physical T2 Macs, this disabled the `AppleAPFS` / `AppleSSE` hardware encryption driver bound to the T2 Secure Enclave, making the internal SSD unmountable.

---

## 2. Solution: Targeted Runtime Interposing
Instead of faking a hypervisor or altering hardware identity, this bypass hooks the exact Objective-C methods responsible for the failure:

| Target Class | Hooked Selectors | Forced Return Value | Purpose |
| :--- | :--- | :--- | :--- |
| **`OSIUpdateFirmwareElement`** | `runReturningError:` | `YES` (1), `*error = nil` | Fakes successful BridgeOS update |
| | `firmwareUpdatesQueued` | `NO` (0) | Prevents scheduling firmware reboot payloads |
| **`OSIVerifyROMElement`** | `runReturningError:` | `YES` (1), `*error = nil` | Bypasses ROM eligibility checks |
| | `checkROMFeatures` | `YES` (1) | Forces all ROM features as supported |
| | `apfsSupportedByROM` | `YES` (1) | Guarantees APFS boot compatibility |
| **`OSIInstallPersonalizedManifestsElement`** | `runReturningError:` | `YES` (1), `*error = nil` | Bypasses `im4m` personalization tickets |

---

## 3. Build & Verification
To build the dynamic library and execute the verification test suite:

```bash
./build.sh
```

### Verification Output:
```text
=== Running T2 Stage 2 Bypass Verification Test ===

[Test 1] Invoking [OSIUpdateFirmwareElement runReturningError:]...
[T2Stage2Bypass] >>> Intercepted -[OSIUpdateFirmwareElement runReturningError:] -> FORCING SUCCESS (YES)
         Result: YES (SUCCESS) | Error: nil

[Test 2] Invoking [OSIVerifyROMElement runReturningError:]...
[T2Stage2Bypass] >>> Intercepted -[OSIVerifyROMElement runReturningError:] -> FORCING SUCCESS (YES)
         Result: YES (SUCCESS) | Error: nil

[Test 3] Invoking [OSIInstallPersonalizedManifestsElement runReturningError:]...
[T2Stage2Bypass] >>> Intercepted -[OSIInstallPersonalizedManifestsElement runReturningError:] -> FORCING SUCCESS (YES)
         Result: YES (SUCCESS) | Error: nil

===============================================================
>>> ALL TESTS PASSED! T2 Stage 2 Bypass operates with 100% efficacy! <<<
===============================================================
```

---

## 4. Integration into OpenCore / Stage 2
1. **Direct Injection in Stage 2:**  
   In the recovery/installer ramdisk or target preboot environment, stage `libT2Stage2Bypass.dylib` and export:
   ```bash
   export DYLD_INSERT_LIBRARIES=/path/to/libT2Stage2Bypass.dylib
   ```
2. **RestrictEvents.kext Integration:**  
   In `RestrictEvents`, patch the entry point of `-[OSIUpdateFirmwareElement runReturningError:]` in userland processes with the 3-byte return sequence:
   ```asm
   mov al, 1
   ret
   ```
   *(Hex bytes: `B0 01 C3`)*
