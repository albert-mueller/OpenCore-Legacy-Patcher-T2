# OpenCore Legacy Patcher Privileged Helper Tool

`com.albert-mueller.opencore-patcher-t2.privileged-helper` is OpenCore Legacy Patcher's Privileged Helper Tool.

The architecture is as such:
1. The main application (OpenCore-Patcher-T2.app) will send arguments to the privileged helper tool to execute.
2. The privileged helper tool will check the code signature of the main application to ensure it is signed by Dortania.
3. The privileged helper tool will then execute the command and return the output to the main application.

The helper tool is able to execute code as root by using the "Set UID" bit present on the file.


## Running from source

Since running OpenCore Legacy Patcher from source will lack Dortania's code signature, you will need to disable code signature verification in the privileged helper tool otherwise root commands will fail.

To do so, compile the privileged helper tool with debug:
```
make debug
```

Then when you build OpenCore-Patcher-T2.pkg, the debug version of the helper tool will be used.


### Security Considerations

When using the Privileged Helper Tool from source, you are now adding a security risk to your system. By disabling the code signature checks, any malicious application is given ability to execute code as root.

If possible, we highly recommend creating a developer account with Apple and signing the application with your own ["Developer ID Application" certificate](https://developer.apple.com/help/account/create-certificates/create-developer-id-certificates/). This will allow you to run the application without disabling code signature checks.

* Note that Dortania's Team ID will need to be replaced in main.m with your own Team ID (`S74BDJXQMD` -> `YOUR_TEAM`)
* Additionally you will be required to compile OpenCore-Patcher-T2.app with your own Developer ID Application certificate

If this is not possible, we recommend using [OpenCore Legacy Patcher's prebuilt binaries](../../SOURCE.md) instead.

## Self signing Priveleged Helper Tool - prefered over make debug
Self signing the Priveleged Helper Tool is prefered to running make debug, as it doesn't come with security compromises while giving the ability to use it without paying the Apple Tax. To do so, you need to compile the Priveleged Helper Tool like this, after you have created a self signed certificate via the Keychain app (doesn't matter if you're running High Sierra, Sequoia or Tahoe, on all of them it works just fine):

## macOS 11 Big Sur and newer:

cd ci_tooling/privileged_helper_tool # (replace this with the path of the Priveleged Helper Tool folder)

make                                   # release build, keeps the certificate check

codesign -f -s "OCLP Self Signed" com.albert-mueller.opencore-patcher-t2.privileged-helper

codesign -dvvv com.albert-mueller.opencore-patcher-t2.privileged-helper 2>&1 | grep Authority

sudo ./install.sh                      # copies to /Library/PrivilegedHelperTools + sets the setuid bit

## macOS 10.15 Catalina and older:

cd ci_tooling/privileged_helper_tool

clang -framework Foundation -framework Security -arch x86_64 \
  -mmacosx-version-min=10.9 -o com.albert-mueller.opencore-patcher-t2.privileged-helper main.m
  
codesign -f -s "OCLP Self Signed" --timestamp=none com.albert-mueller.opencore-patcher-t2.privileged-helper

sudo ./install.sh

## If you encounter any issues with self signing the app afterwards, run this:

chmod +x ci_tooling/create-signing-certificate.sh

And then:

create-signing-certificate.sh

