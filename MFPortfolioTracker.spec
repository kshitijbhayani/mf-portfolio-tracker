# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for MF Portfolio Tracker (one-file, windowed)."""

from PyInstaller.utils.hooks import collect_all

datas, binaries, hiddenimports = [], [], []
# Bundle the CAS-parsing stack so import works without a separate pip install.
# casparser_isin ships a ~48 MB ISIN->AMFI SQLite db that NSDL/CDSL enrichment
# opens at runtime via __file__ — collect_all keeps it at casparser_isin/isin.db.
for pkg in ("casparser", "casparser_isin", "fitz", "pymupdf", "pydantic", "pydantic_core"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

# App resources.
datas += [("icon.ico", "."), ("icon.png", ".")]

block_cipher = None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["PyQt5", "PyQt6", "PySide2", "PySide6", "matplotlib", "numpy.testing"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="MF Portfolio Tracker",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="icon.ico",
)
