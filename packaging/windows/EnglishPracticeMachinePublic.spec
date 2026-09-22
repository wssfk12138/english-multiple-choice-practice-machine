from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

project_root = Path(SPECPATH).parents[1]
name = 'EnglishPracticeMachinePublic'
a = Analysis(
    [str(project_root / 'run_app.py')],
    pathex=[str(project_root)],
    binaries=[],
    datas=[
        (str(project_root / 'frontend/dist'), 'frontend/dist'),
        (str(project_root / 'docs/schemas/esq-1.0.schema.json'), 'docs/schemas'),
        (str(project_root / 'backend/app/services/convert_word.ps1'), 'backend/app/services'),
        (str(project_root / 'examples/bundled-banks'), 'examples/bundled-banks'),
    ],
    hiddenimports=['backend.app.main'] + collect_submodules('zeroconf'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(project_root / 'packaging/windows/runtime_streams.py')],
    excludes=['pytest', 'unittest', 'tkinter'],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name=name,
    debug=False, bootloader_ignore_signals=False, strip=False, upx=True,
    console=False, disable_windowed_traceback=False, argv_emulation=False,
    icon=str(project_root / 'frontend/public/assets/icons/brand-mark.ico'),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name=name)
