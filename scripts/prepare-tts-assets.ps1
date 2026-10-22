param(
    [string[]]$Accents = @('us')
)

$ErrorActionPreference = 'Stop'
if ($Accents -contains 'gb') {
    throw 'Public build must not include en_GB/Alan assets.'
}
$root = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
function Assert-WorkspacePath([string]$Path) {
    $resolved = [IO.Path]::GetFullPath($Path)
    if (-not $resolved.StartsWith($root + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Computed TTS path is outside the workspace.'
    }
    return $resolved
}
$targetRoot = Assert-WorkspacePath (Join-Path $root 'tts')
$cache = Assert-WorkspacePath (Join-Path $root '.tts-cache')
New-Item -ItemType Directory -Force -Path $targetRoot, $cache | Out-Null

$item = @{
    Name = 'vits-piper-en_US-ljspeech-medium-int8'
    Target = 'en_US'
    Model = 'en_US-ljspeech-medium.onnx'
    Url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/tts-models/vits-piper-en_US-ljspeech-medium-int8.tar.bz2'
    Hash = '24DC3BD77DD48C291E52C297878D3437C9492F245D823D7F6A06C4BBB67F4B6B'
}

$archive = Join-Path $cache ($item.Name + '.tar.bz2')
if (Test-Path -LiteralPath $archive) {
    $actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
    if ($actual -ne $item.Hash) { Move-Item -LiteralPath $archive -Destination ($archive + '.invalid-' + [Guid]::NewGuid().ToString('N')) }
}
if (-not (Test-Path -LiteralPath $archive)) {
    $download = Assert-WorkspacePath ($archive + '.download-' + [Guid]::NewGuid().ToString('N'))
    & curl.exe -L --fail --retry 3 --output $download $item.Url
    if ($LASTEXITCODE -ne 0) { throw "Download failed: $($item.Url)" }
    if ((Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash -ne $item.Hash) { throw 'Downloaded archive SHA-256 mismatch; existing model retained.' }
    Move-Item -LiteralPath $download -Destination $archive
}
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash
if ($actual -ne $item.Hash) {
    throw "SHA-256 mismatch for $archive. Expected $($item.Hash), actual $actual"
}

$extract = Assert-WorkspacePath (Join-Path $cache ($item.Name + '-' + [Guid]::NewGuid().ToString('N')))
New-Item -ItemType Directory -Force -Path $extract | Out-Null
& tar.exe -xjf $archive -C $extract
if ($LASTEXITCODE -ne 0) { throw "Unable to extract $archive" }

$source = Join-Path $extract $item.Name
$finalTarget = Assert-WorkspacePath (Join-Path $targetRoot $item.Target)
$target = Assert-WorkspacePath (Join-Path $extract 'prepared')
New-Item -ItemType Directory -Force -Path $target | Out-Null
Copy-Item -LiteralPath (Join-Path $source $item.Model) -Destination (Join-Path $target 'model.onnx')
Copy-Item -LiteralPath (Join-Path $source 'tokens.txt') -Destination (Join-Path $target 'tokens.txt')
Copy-Item -LiteralPath (Join-Path $source 'MODEL_CARD') -Destination (Join-Path $target 'MODEL_CARD')
Copy-Item -LiteralPath (Join-Path $source 'espeak-ng-data') -Destination (Join-Path $target 'espeak-ng-data') -Recurse
$json = Get-ChildItem -LiteralPath $source -Filter '*.onnx.json' | Select-Object -First 1
if ($json) { Copy-Item -LiteralPath $json.FullName -Destination (Join-Path $target 'model.onnx.json') }


    if (-not (Test-Path -LiteralPath (Join-Path $target 'model.onnx')) -or
        -not (Test-Path -LiteralPath (Join-Path $target 'tokens.txt')) -or
        -not (Test-Path -LiteralPath (Join-Path $target 'espeak-ng-data'))) {
        throw 'Prepared voice is incomplete; existing voice retained.'
    }
    $backup = Assert-WorkspacePath (Join-Path $cache ('previous-' + $item.Target + '-' + [Guid]::NewGuid().ToString('N')))
    $hadPrevious = Test-Path -LiteralPath $finalTarget
    if ($hadPrevious) { Move-Item -LiteralPath $finalTarget -Destination $backup }
    try { Move-Item -LiteralPath $target -Destination $finalTarget }
    catch {
        if ($hadPrevious -and -not (Test-Path -LiteralPath $finalTarget)) {
            Move-Item -LiteralPath $backup -Destination $finalTarget
        }
        throw
    }

$bytes = (Get-ChildItem -LiteralPath $targetRoot -Recurse -File | Measure-Object -Property Length -Sum).Sum
"TTS public assets ready: {0:N1} MiB at {1}" -f ($bytes / 1MB), $targetRoot
