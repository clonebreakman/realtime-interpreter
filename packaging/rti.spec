# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Windows desktop app.

Build:
    .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean packaging\rti.spec
Output:
    dist\RTI\RTI.exe
"""
from pathlib import Path

ROOT = Path(SPECPATH).parent
WEB = ROOT / "web"
CONFIG = ROOT / "config.json"

DATAS = [(str(WEB), "web")] if WEB.is_dir() else []
if CONFIG.is_file():
    DATAS.append((str(CONFIG), "."))

a = Analysis(
    [str(ROOT / "scripts" / "desktop_entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=DATAS,
    hiddenimports=[
        "uvicorn.logging",
        "uvicorn.loops.auto",
        "uvicorn.loops.asyncio",
        "uvicorn.protocols.http.auto",
        "uvicorn.protocols.http.h11_impl",
        "uvicorn.protocols.websockets.auto",
        "uvicorn.protocols.websockets.websockets_impl",
        "uvicorn.lifespan.on",
        "websockets",
        "websockets.asyncio",
        "websockets.asyncio.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["sounddevice", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RTI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="RTI",
)
