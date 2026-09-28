# -*- mode: python ; coding: utf-8 -*-

import os
import sys
import time
import subprocess
from pathlib import Path

# Fix: Use PyInstaller's built-in global 'SPECPATH' instead of '__file__'
try:
    SPEC_DIR = Path(SPECPATH).resolve()
except NameError:
    SPEC_DIR = Path(os.getcwd()).resolve()

sys.path.append(str(SPEC_DIR))

from opencore_legacy_patcher import constants

block_cipher = None

# Fix: Use the corrected SPEC_DIR absolute variable
datas = [
   (str(SPEC_DIR / 'payloads.dmg'), '.'),
   (str(SPEC_DIR / 'Universal-Binaries.dmg'), '.'),
]

# Fix: Use the corrected SPEC_DIR absolute variable
if (SPEC_DIR / "DortaniaInternalResources.dmg").exists():
   datas.append((str(SPEC_DIR / 'DortaniaInternalResources.dmg'), '.'))


a = Analysis([str(SPEC_DIR / 'OpenCore-Patcher-GUI.command')],
             pathex=[],
             binaries=[],
             datas=datas,
             hiddenimports=[],
             hookspath=[],
             hooksconfig={},
             runtime_hooks=[],
             excludes=['_curses', 'curses'],
             win_no_prefer_redirects=False,
             win_private_assemblies=False,
             cipher=block_cipher,
             noarchive=False)

pyz = PYZ(a.pure,
          a.zipped_data,
          cipher=block_cipher)

exe = EXE(pyz,
          a.scripts,
          [],
          exclude_binaries=True,
          name='OpenCore-Patcher-T2',
          debug=False,
          bootloader_ignore_signals=False,
          strip=False,
          upx=True,
          console=False,
          disable_windowed_traceback=False,
          target_arch=None,  
          codesign_identity=None,
          entitlements_file=None)

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               strip=False,
               upx=True,
               upx_exclude=[],
               name='OpenCore-Patcher-T2')

app = BUNDLE(coll,
             name='OpenCore-Patcher-T2.app',
             icon=str(SPEC_DIR / "payloads/Resources/AppIcons/OC-Patcher.icns"), # Fix: Use the corrected SPEC_DIR variable and Resources path
             bundle_identifier="com.dortania.opencore-legacy-patcher-t2",
             info_plist={
                "CFBundleName": "OpenCore Legacy Patcher T2",
                "CFBundleVersion": constants.Constants().patcher_version,
                "CFBundleShortVersionString": constants.Constants().patcher_version,
                "NSHumanReadableCopyright": constants.Constants().copyright_date,
                "LSMinimumSystemVersion": "10.13.6",
                "NSRequiresAquaSystemAppearance": False,
                "NSHighResolutionCapable": True,
                "Build Date": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "BuildMachineOSBuild": subprocess.run(["/usr/bin/sw_vers", "-buildVersion"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode().strip(),
                "NSPrincipalClass": "NSApplication",
             })
