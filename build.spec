# build.spec  —  PyInstaller build script for MediaToolkit Pro
#
# Usage:
#   pyinstaller build.spec
#
# For maximum source-code protection, obfuscate with PyArmor first:
#   pip install pyarmor
#   pyarmor gen app.py          # outputs to dist/pyarmor_runtime_*/
#   # edit the 'script' path below to point at the obfuscated copy
#   pyinstaller build.spec
#
# The resulting executable will be in:   dist/MediaToolkitPro/

import sys, os

block_cipher = None   # PyInstaller ≥ 5 removed the --key cipher; use PyArmor instead.

a = Analysis(
    ['app.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        # If you add an icon file, include it:
        # ('icon.ico', '.'),
    ],
    hiddenimports=[
        # faster-whisper / ctranslate2 sometimes need these:
        'ctranslate2',
        'faster_whisper',
        'huggingface_hub',
        'tokenizers',
        # pygame
        'pygame',
        'pygame.mixer',
        # PySide6 extras (Qt platform plugins etc.)
        'PySide6.QtSvg',
        'PySide6.QtXml',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Not needed — keeps the binary smaller
        'yt_dlp',
        'tkinter',
        'matplotlib',
        'scipy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='MediaToolkitPro',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,           # compress with UPX if installed: https://upx.github.io/
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,      # no console window
    disable_windowed_traceback=True,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    # icon='icon.ico',  # uncomment and add your .ico file
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='MediaToolkitPro',
)
