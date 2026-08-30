$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv")) {
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($py) {
        & $py.Source -3.12 -m venv .venv
    }
    else {
        $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
        if (-not $python) {
            throw "Python 3.12 is required. Install Python and run setup.ps1 again."
        }
        & $python.Source -m venv .venv
    }
}

& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

$distIndex = Join-Path $projectRoot "frontend\dist\index.html"
if (Test-Path -LiteralPath $distIndex) {
    # 发布包自带已构建的前端；仅源码开发时才需要 Node 构建。
    Write-Host "Found prebuilt frontend (frontend\dist); skipping pnpm build."
}
else {
    $corepack = Get-Command "corepack.cmd" -ErrorAction SilentlyContinue
    if (-not $corepack) {
        $corepack = Get-Command "corepack.exe" -ErrorAction SilentlyContinue
    }
    if (-not $corepack) {
        throw "Node.js with Corepack is required. Install Node.js and run setup.ps1 again."
    }

    Push-Location frontend
    try {
        & $corepack.Source pnpm install --frozen-lockfile
        & $corepack.Source pnpm run build
    }
    finally {
        Pop-Location
    }
}

$pythonw = Join-Path $projectRoot ".venv\Scripts\pythonw.exe"
$launcher = Join-Path $projectRoot "run_app.py"
$icon = Join-Path $projectRoot "frontend\public\assets\icons\brand-mark.ico"
$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "英语刷题机.lnk"

foreach ($requiredPath in @($pythonw, $launcher, $icon)) {
    if (-not (Test-Path -LiteralPath $requiredPath)) {
        throw "Desktop shortcut dependency not found: $requiredPath"
    }
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $launcher + '"'
$shortcut.WorkingDirectory = $projectRoot
$shortcut.IconLocation = $icon + ",0"
$shortcut.WindowStyle = 7
$shortcut.Description = "英语刷题机"
$shortcut.Save()

Write-Host "Setup complete. Desktop shortcut: $shortcutPath"
