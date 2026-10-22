from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

project_root = Path(SPECPATH).parents[1]
name = 'EnglishPracticeMachinePublic'
if (project_root / 'tts/en_GB').exists():
    raise RuntimeError('Public build must not contain en_GB/Alan assets')
if not (project_root / 'tts/en_US/model.onnx').is_file():
    raise RuntimeError('Prepare the public US voice before packaging')
a = Analysis(
    [str(project_root / 'run_app.py')],
    pathex=[str(project_root)],
    binaries=collect_dynamic_libs('sherpa_onnx'),
    datas=[
        (str(project_root / 'tts/en_US'), 'tts/en_US'),
        *collect_data_files('sherpa_onnx'),
        (str(project_root / 'frontend/dist'), 'frontend/dist'),
        (str(project_root / 'docs/schemas/esq-1.0.schema.json'), 'docs/schemas'),
        (str(project_root / 'backend/app/services/convert_word.ps1'), 'backend/app/services'),
        (str(project_root / 'examples/bundled-banks'), 'examples/bundled-banks'),
    ],
    hiddenimports=['backend.app.main'] + collect_submodules('zeroconf') + collect_submodules('sherpa_onnx'),
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
