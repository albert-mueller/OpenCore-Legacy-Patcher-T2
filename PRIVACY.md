# Privacy Policy

BridgePatcher does not collect analytics, usage statistics or crash reports, and does not send any information about your Mac to Dortania, to this project or to anyone else.

Dortania's OpenCore Legacy Patcher, which BridgePatcher is based on, contained an analytics and crash reporting component that could send a hashed system UUID, the app version, the macOS version, the Mac model, GPUs, firmware vendor and the country set in System Settings to an analytics server. This component has been removed from BridgePatcher entirely, together with the "Allow Reporting" setting.

Crash and error logs are only stored locally on your Mac. They are never uploaded; if you want to share one, for example in a GitHub issue, you do so yourself.

## Network connections

BridgePatcher still connects to the internet to do its job, for example to check for and download updates, PatcherSupportPkg, Kernel Debug Kits, Metallibs, macOS installers and the AppleDB catalog. These requests go to services such as GitHub, Apple, Dortania's Kernel Debug Kit and Metallib catalogs and AppleDB. Like any download, they reveal your IP address and the app's user agent (which contains the BridgePatcher version) to the server you download from, but no information about your system is sent along with them.
