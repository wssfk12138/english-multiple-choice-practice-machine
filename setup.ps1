$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath ".venv")) {
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue

    # Resolve the Python used to create the virtual environment:
    # 1. py -3.12 (project-verified version)
    # 2. python from PATH
    # 3. common install paths (in case PATH is stale)
    $venvPython = $null
    $venvPyVersion = $null
    if ($py) {
        try {
            & $py.Source -3.12 -c "pass" 2>$null
            if ($LASTEXITCODE -eq 0) {
                $venvPython = $py.Source
                $venvPyVersion = "-3.12"
            }
        }
        catch {
            # py -3.12 not available; fall through to next option
        }
    }
    if (-not $venvPython -and $python) {
        $venvPython = $python.Source
    }
    if (-not $venvPython) {
        $venvPython = @(
            "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
            "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
            "C:\Python313\python.exe",
            "C:\Python312\python.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    }
    if (-not $venvPython) {
        throw "Python is required. Install Python 3.12+ and run setup.ps1 again."
    }
    if ($venvPyVersion) {
        & $venvPython $venvPyVersion -m venv .venv
    }
    else {
        & $venvPython -m venv .venv
    }
}

& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt

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

Write-Host "Setup complete. Run start.ps1 to launch English Practice Machine."
