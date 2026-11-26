# SessionCost installer for Windows (PowerShell):
# Recommended (no remote script execution):
#   python -m pip install sessioncost==1.2.9 && python -m sessioncost setup
#   uv tool install sessioncost==1.2.9 && sessioncost setup
# Corporate / zero-network mode: add `--offline` to `setup` (or set `$env:SESSIONCOST_OFFLINE = '1'`).
# First-time install only. To update an existing install, run `sessioncost update` (or `/sessioncost update` in a chat) — do not re-run the installer.
$ErrorActionPreference = 'Stop'
$ver = if ($env:SESSIONCOST_VERSION) { $env:SESSIONCOST_VERSION } else { '1.2.9' }
$src = if ($env:SESSIONCOST_SOURCE) { $env:SESSIONCOST_SOURCE } else { "sessioncost==$ver" }
$offline = ($args -contains '--offline') -or ($env:SESSIONCOST_OFFLINE -match '^(1|true|yes|on)$')
$setupArgs = if ($offline) { @('setup', '--offline') } else { @('setup') }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host 'Installing uv via winget...'
        winget install --id=astral-sh.uv -e --accept-source-agreements --accept-package-agreements
        $env:Path = "$HOME\.local\bin;$env:LOCALAPPDATA\Microsoft\WinGet\Links;$env:Path"
    }
}

if (Get-Command uv -ErrorAction SilentlyContinue) {
    Write-Host "Installing SessionCost ($src) with uv..."
    uv tool install --quiet --reinstall --python '>=3.10' $src
    if ($LASTEXITCODE -ne 0) { throw 'SessionCost could not be installed (see the message above).' }
    & (Join-Path (uv tool dir --bin) 'sessioncost.exe') @setupArgs
    exit $LASTEXITCODE
}

$py = Get-Command python -ErrorAction SilentlyContinue
if (-not $py) { $py = Get-Command py -ErrorAction SilentlyContinue }
if ($py) {
    Write-Host "Installing SessionCost ($src) with pip..."
    & $py.Source -m pip install --user $src
    if ($LASTEXITCODE -ne 0) { throw 'SessionCost could not be installed (see the message above).' }
    & $py.Source -m sessioncost @setupArgs
    exit $LASTEXITCODE
}

throw 'Neither uv nor Python 3.10+ was found. Install uv (e.g. `winget install astral-sh.uv`) or Python 3.10+, then run `pip install sessioncost==1.2.9` and `python -m sessioncost setup`.'
