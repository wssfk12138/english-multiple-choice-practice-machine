# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置：生成单文件便携版「英语刷题机.exe」。

用法（在项目根目录，已激活 .venv）：
    pyinstaller english_practice_machine.spec --noconfirm
"""

from pathlib import Path

project_root = Path(SPECPATH).resolve()

a = Analysis(
    ["run_app.py"],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / "frontend" / "dist"), "frontend/dist"),
        (str(project_root / "examples" / "bundled-banks"), "examples/bundled-banks"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="英语刷题机",
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
)
