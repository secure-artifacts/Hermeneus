# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs

block_cipher = None

HEAVY_PACKAGES = [
    "funasr",
    "faster_whisper",
    "torch",
    "ctranslate2",
]

datas = []
binaries = []
hiddenimports = []

for pkg in HEAVY_PACKAGES:
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

binaries += collect_dynamic_libs("ctranslate2")

hiddenimports += [
    "funasr.models",
    "funasr.utils",
    "torch._C",
    "torch.utils.checkpoint",
]

excludes = [
    "torch.utils.tensorboard",
    "torch.testing",
    "matplotlib",
    "tkinter",
    "IPython",
    "notebook",
    "pytest",
]

a = Analysis(
    ["server.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="asr_server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    target_arch="arm64",
)
