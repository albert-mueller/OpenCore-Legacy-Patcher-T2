# Build from source

BridgePatcher at its core is a Python-based GUI/CLI-based application. In turn, to run the project from source, you simply need to invoke the OpenCore-Patcher-GUI.command file via Python.

For developers wishing to validate mainline changes, you may use this link: [GUI (Graphical Based App)](https://github.com/bridgepatcher/BridgePatcher/releases)

* **Warning**:Untagged builds built from the latest commit are actively developed BridgePatcher builds. These builds have not been tested, are not guaranteed to work, and are not guaranteed to be safe. They may have bugs that remain to be tested. Do not use untagged builds without a good reason to do so, and do not use them on your main machine. Additionally, these binaries should not be used without first consulting the [CHANGELOG](./CHANGELOG.md).

  **Do not share _any_ links to these binaries** in forums; please link to **this document only**.
  * Additionally, do not reupload these binaries or download binaries from other sites. Using binaries from untrusted sources is a security issue, as they may have malware or exploitable vulnerabilities that are patched long time ago. 
* Users running new builds of the project without understanding what has changed and the implications of installing software under active development are at a higher risk of bricking their installation as they do not read any warnings provided in the CHANGELOG. We wish to minimize these situations as much as possible.

## Getting Started

To get started, you need to ensure you meet the minimum requirements:

## Minimum requirements for building the patcher
- A 64 bit Intel Mac or Apple Silicon Mac
- macOS 10.13.6 High Sierra or newer, for compatability with versions earlier than Sonoma, you need to use a host or VM that runs that older version
- Python 3.13 (Python 3.13 is tested only and recommended for use, will be covered in the guide how to install Python)
- MacPorts to be installed (will be covered in the guide)
- OpenSSL to be installed from MacPorts (if you have the one from Homebrew, it may not work at all) (will be installed automatically while building the patcher if not done yet)
- self signed or Apple Developer certificates are preffered for the best security pracitce
- The following dependencies to be installed: requests, pyobjc, wxpython (older than 4.2.5), pyinstaller, packaging, py_sip_xnu, py-applescript, markdown2, macos-pkg-builder, mac-signing-buddy, rich and all other dependencies if there are any newly added dependencies in requirements.txt (will be covered in the guide)

## 1. Install and configure Python 3.13
1. Go to https://www.python.org/downloads/
2. Next to Python 3.13, click on Download
<img width="3104" height="1812" alt="image" src="https://github.com/user-attachments/assets/1840e9fd-306f-41c7-9eaf-a5d5ab732365" />
3. Click on Download macOS installer
<img width="3104" height="1812" alt="image" src="https://github.com/user-attachments/assets/65955dc2-11bd-4123-abc8-54f73d7a4b40" />
4. Open the installer
5. Click on Continue / Fortfahren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/40b3ef6b-0d37-4ee2-ac17-a4589e92f08d" />
6. Click Continue / Fortfahren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/6ebfef5e-527c-4fa4-98a3-4f61c09bca03" />
7. Click Fortfahren / Continue
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/e04f012a-9064-459c-9006-1d15051057af" />
8. Accept the license by clicking Accept / Akzeptieren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/00fa4bb5-b098-4c4a-9d6b-4c100a5f6d67" />
9. Click Install / Installieren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/58250b14-0046-407b-85f7-439e78b9a19f" />
10. During installation, this folder will open up, Don't close it, we'll need that for later.
<img width="2064" height="1096" alt="image" src="https://github.com/user-attachments/assets/298fce88-fe68-45b1-aa32-1dc7d2311787" />
11. Once installation is done, click on Close / Schließen
<img width="1578" height="1120" alt="image" src="https://github.com/user-attachments/assets/b3e95e04-1701-44ed-a131-1b65dfbd5fc8" />
12. Go back to that folder which got opened automatically
13. Double click on Install Certificates.command to install the certificates
<img width="2064" height="1096" alt="image" src="https://github.com/user-attachments/assets/70a463e9-43a4-4318-afd9-b547c250c928" />



## 2. Install MacPorts
1. Go to https://guide.macports.org/
2. Under macOS Package Installer, select the version of macOS that you're currently running
<img width="3104" height="1812" alt="image" src="https://github.com/user-attachments/assets/b7460abb-d9b0-48d6-8cbc-2d2bf01005de" />
3. Open the installer
4. Click Continue / Fortfahren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/27d9c7db-a86e-4540-9ac9-a3215a0169ca" />
5. Click Continue / Fortfahren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/fc797e09-b9e0-4f50-b3b1-e4fd47acac09" />
6. Click Continue / Fortfahren
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/8f8f4ac0-c34a-4902-8e47-853ebe6fca07" />
7. Accept the license agreement by clicking Akzeptieren / Accept
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/3b923d51-8fd5-4388-9483-aa2a8acc5477" />
8. Click on Installieren / Install
<img width="1464" height="1120" alt="image" src="https://github.com/user-attachments/assets/1666d69c-d31e-437b-bade-6b9bb8bef2a0" />
9. Once done, click on Close / Schließen
<img width="1578" height="1120" alt="image" src="https://github.com/user-attachments/assets/a83dd6ac-2807-4d8b-b735-7f7cccfad45e" />

## 3. Clone BridgePatcher repo to your computer
1. Open the Terminal and write the following command: git clone https://github.com/bridgepatcher/BridgePatcher

## 4. Install all necessary dependencies
All required dependencies, except OpenSSL, will be installed like this. OpenSSL will be installed during the build process.
1. Write in the Terminal the following command: cd /Users/[yourusername]/BridgePatcher, but replace [yourusername] with your own user folder name
2. Write in the Terminal the following command afterwards: pip3 install -r requirements.txt

## 5. Create a self signed certificate if you don't use an Apple Developer Certificate (recommended) unless you're going to build a Debug version
1. Write the following command and hit Enter: /Users/[yourusername]/BridgePatcher/ci_tooling/privileged_helper_tool/create-signing-certificate.sh, but replace [yourusername] with your user folder name
2. Follow the on-screen instructions

## 6. Create a Priveleged Helper Tool
To prevent attackers from delivering malware using my own signature via LoTL techniques, I removed the prebuilt Priveleged Helper Tool since alpha 19.9. Now you have to build it yourself. I recommend the same for your repository: to prevent attackers from abusing your signature for executing malware as root, which is very dangerous and can take over victim's computers, I don't recommend publishing a prebuilt Priveleged Helper Tool in your repository as well.
1. Write the following command in the Terminal: cd cd /Users/[yourusername]/BridgePatcher/ci_tooling/privileged_helper_tool, but replace [yourusername] with your user folder's name
2. To build a release version (recommended), write the following command in the Terminal and hit Enter: make release and hit Enter.
3. To build a Debug version, write the following command in the Terminal and hit Enter: make debug. However, making a Debug version of the Priveleged Helper Tool as Debug is not recommended as attackers can at this point execute malicious code from any application as root.

## 7. Build the patcher
1. Write the following command in the Terminal: cd. Just plain cd to exit the Priveleged Helper Tool directory
2. Write the following command: python3 /Users/[yourusername]/BridgePatcher/Build-Project.command , but replace [yourusernam] with your user folder's name
3. If needed, pass the following arguments:
--update-channel medelcartelinc if you want to set the update channel to Matteo's fork
Else, you don't need to pass any update arguments, it will default to the official project
--ignore-release if there is already an existing version in the mainstream repository that matches the version in your fork you want to release to ignore the checks
4. Hit Enter
5. Follow the onscreen instructions
