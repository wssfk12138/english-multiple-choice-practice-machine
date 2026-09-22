# Windows Public Build

Build with Python dependencies from requirements.txt and requirements-build.txt,
then build frontend/dist using the pinned frontend package manager.

```powershell
python -m PyInstaller --distpath outputs/windows-public/dist --workpath outputs/windows-public/work packaging/windows/EnglishPracticeMachinePublic.spec
```

Use a new output directory per candidate; do not overwrite a running build.
The entry point is EnglishPracticeMachinePublic/EnglishPracticeMachinePublic.exe.
The public launcher uses port 8775, LAN HTTP 8776 and pinned TLS 8777.
Frozen user data stays in LOCALAPPDATA/EnglishPracticeMachinePublic, separate
from internal data. No legacy data migration is performed by this variant.
ENGLISH_PRACTICE_DATA_DIR and ENGLISH_PRACTICE_PORT can isolate acceptance runs.

Only frontend assets, the ESQ schema, the Word converter and the two public
starter banks are bundled. Never include backend/data, private banks, logs,
credentials or development evidence. GitHub publication remains a separate step.

Resource lookup follows the existing internal freezer and
https://pyinstaller.org/en/v6.22.0/runtime-information.html .

## Installer

Compile installer.iss with Inno Setup 6 after producing the frozen directory:

```powershell
& "$env:LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe" "/DMyAppVersion=0.1.0" "/DSourceDir=<absolute frozen directory>" "/DOutputDir=<absolute output directory>" packaging/windows/installer.iss
if ($LASTEXITCODE -ne 0) { throw "Installer build failed" }
```

The public installer has its own stable AppId, per-user installation directory
and shortcuts. It does not install or remove internal-edition files. Neither
upgrades nor uninstall remove LOCALAPPDATA/EnglishPracticeMachinePublic.
Version defaults match frontend/package.json; select the version explicitly
for a release candidate. Compilation alone is not installation acceptance.
Inno Setup documentation: https://jrsoftware.org/ishelp/index.php .
