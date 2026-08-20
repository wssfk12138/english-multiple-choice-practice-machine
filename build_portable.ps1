# 一键打包便携版「英语刷题机.exe」
# 用法：右键“使用 PowerShell 运行”，或在项目根目录执行  .\build_portable.ps1
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontendDist = Join-Path $projectRoot "frontend\dist"

Write-Host "=== 英语刷题机 便携版打包 ===" -ForegroundColor Cyan

# 1. 检查 Python 虚拟环境
if (-not (Test-Path -LiteralPath $python)) {
    Write-Host "[1/3] 未找到虚拟环境，先运行 setup.ps1 初始化环境..." -ForegroundColor Yellow
    & (Join-Path $projectRoot "setup.ps1")
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python 虚拟环境创建失败，请先手动运行 setup.ps1"
}
Write-Host "[1/3] Python 虚拟环境 OK" -ForegroundColor Green

# 2. 检查/构建前端产物
if (-not (Test-Path -LiteralPath (Join-Path $frontendDist "index.html"))) {
    Write-Host "[2/3] 前端产物缺失，开始构建前端..." -ForegroundColor Yellow
    $corepack = Get-Command "corepack.cmd" -ErrorAction SilentlyContinue
    if (-not $corepack) { $corepack = Get-Command "corepack.exe" -ErrorAction SilentlyContinue }
    if (-not $corepack) { throw "需要 Node.js + Corepack 才能构建前端，请先安装 Node.js" }
    Push-Location (Join-Path $projectRoot "frontend")
    try {
        & $corepack.Source pnpm install
        & $corepack.Source pnpm run build
    }
    finally {
        Pop-Location
    }
}
Write-Host "[2/3] 前端产物 OK" -ForegroundColor Green

# 3. 安装 pyinstaller 并打包
Write-Host "[3/3] 安装 pyinstaller 并打包..." -ForegroundColor Yellow
& $python -m pip install --quiet pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pyinstaller 安装失败" }

Set-Location -LiteralPath $projectRoot
& $python -m PyInstaller english_practice_machine.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 打包失败" }

$exe = Join-Path $projectRoot "dist\英语刷题机.exe"
if (Test-Path -LiteralPath $exe) {
    Write-Host ""
    Write-Host "打包成功！" -ForegroundColor Green
    Write-Host "便携版位置：$exe" -ForegroundColor Cyan
    Write-Host "双击即可运行，无需安装 Python / Node.js。" -ForegroundColor Cyan
    Write-Host "使用数据（题库、错题、单词本）保存在 exe 同目录的 backend\data 文件夹中。" -ForegroundColor Cyan
} else {
    throw "未找到打包产物，请检查上方日志"
}
