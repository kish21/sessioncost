# SessionCost installer for Windows (PowerShell):
#   irm https://raw.githubusercontent.com/kish21/sessioncost/main/install.ps1 | iex
# Uses uv (https://docs.astral.sh/uv/), which brings its own Python when this computer has none.
# Run it again to update.
$ErrorActionPreference = 'Stop'
$src = if ($env:SESSIONCOST_SOURCE) { $env:SESSIONCOST_SOURCE } else { 'https://github.com/kish21/sessioncost/archive/refs/heads/main.zip' }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host 'Installing uv (it fetches Python for SessionCost if needed)...'
    powershell -NoProfile -ExecutionPolicy ByPass -Command 'irm https://astral.sh/uv/install.ps1 | iex'
    $env:Path = "$HOME\.local\bin;$env:Path"
}

Write-Host 'Installing SessionCost...'
uv tool install --quiet --reinstall --python '>=3.10' $src
if ($LASTEXITCODE -ne 0) { throw 'SessionCost could not be installed (see the message above).' }
& (Join-Path (uv tool dir --bin) 'sessioncost.exe') setup
