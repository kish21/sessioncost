# SessionCost guard launcher for Windows hooks (plugin installs without bash on PATH).
# Turned off: exits before starting Python. On (the default): finds Python 3.10+ once (preferring pythonw.exe so no
# console window flashes), caches it, and streams raw UTF-8 stdin/stdout to guard.py with CreateNoWindow = $true.
$ErrorActionPreference = "SilentlyContinue"
$homeDir = if ($env:USERPROFILE) { $env:USERPROFILE } else { $HOME }
$G = if ($env:SESSIONCOST_GUARD_DIR) { $env:SESSIONCOST_GUARD_DIR } else { Join-Path $homeDir ".sessioncost\guard" }
$cfgPath = Join-Path $G "config.json"
$cfg = if (Test-Path -LiteralPath $cfgPath) { Get-Content -LiteralPath $cfgPath -Raw } else { "" }
$ev = if ($args.Count -gt 0) { [string]$args[0] } else { "" }

$need = (Test-Path -LiteralPath (Join-Path $G "pending.json")) -or
        (@(Get-ChildItem -LiteralPath $G -Filter "*.state.json").Count -gt 0) -or
        ($cfg -notmatch '"on"\s*:\s*false') -or
        ($ev -eq "stop" -and $cfg -notmatch '"auto"\s*:\s*false') -or
        (($ev -in @("session-start", "prompt", "stop")) -and $cfg -notmatch '"fix_auto"\s*:\s*false')
if (-not $need) { exit 0 }

$ROOT = Split-Path -Parent $PSScriptRoot
$cachePath = Join-Path $G "python.win"
$PY = $null
$fromCache = $false
if (Test-Path -LiteralPath $cachePath) {
    $cand = (Get-Content -LiteralPath $cachePath -Raw)
    if ($cand) {
        $cand = $cand.Trim()
        if ($cand -and (Get-Command $cand)) {
            $PY = $cand
            $fromCache = $true
        }
    }
}

function Test-Py([string]$exe) {
    $cmd = Get-Command $exe
    if (-not $cmd) { return $null }
    $path = $cmd.Source
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $path
    $psi.Arguments = '-S -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    try {
        $p = [System.Diagnostics.Process]::Start($psi)
        $p.WaitForExit(5000) | Out-Null
        if ($p.HasExited -and $p.ExitCode -eq 0) {
            $quiet = Join-Path (Split-Path -Parent $path) "pythonw.exe"
            if (([System.IO.Path]::GetFileName($path) -ieq "python.exe") -and (Test-Path -LiteralPath $quiet)) {
                return $quiet
            }
            return $path
        }
    } catch {}
    return $null
}

if (-not $PY) {
    foreach ($p in @("python", "python3", "py")) {
        $PY = Test-Py $p
        if ($PY) { break }
    }
}
if (-not $PY) {
    $lb = Join-Path $homeDir ".local\bin"
    foreach ($f in @(Get-ChildItem -LiteralPath $lb -Filter "python3*.exe")) {
        $PY = Test-Py $f.FullName
        if ($PY) { break }
    }
}
if (-not $PY) {
    foreach ($u in @("uv", (Join-Path $homeDir ".local\bin\uv.exe"))) {
        $ucmd = Get-Command $u
        if (-not $ucmd) { continue }
        $psi = New-Object System.Diagnostics.ProcessStartInfo
        $psi.FileName = $ucmd.Source
        $psi.Arguments = 'python find --no-project ">=3.10"'
        $psi.UseShellExecute = $false
        $psi.CreateNoWindow = $true
        $psi.RedirectStandardOutput = $true
        $psi.RedirectStandardError = $true
        try {
            $p = [System.Diagnostics.Process]::Start($psi)
            $out = $p.StandardOutput.ReadToEnd().Trim()
            $p.WaitForExit(5000) | Out-Null
            if ($p.HasExited -and $p.ExitCode -eq 0 -and $out) {
                $PY = Test-Py $out
                if ($PY) { break }
            }
        } catch {}
    }
}
if (-not $PY) {
    [Console]::Error.WriteLine("SessionCost guard: no Python 3.10+ found, so it cannot check this step")
    exit 0
}
if (-not $fromCache) {
    if (-not (Test-Path -LiteralPath $G)) {
        New-Item -ItemType Directory -Path $G -Force | Out-Null
    }
    [System.IO.File]::WriteAllText($cachePath, $PY)
}

function Quote-Arg([string]$a) {
    if ($a -match '[\s"]') { '"' + ($a -replace '"', '\"') + '"' } else { $a }
}
$argList = @("-S", (Quote-Arg (Join-Path $ROOT "guard.py")), "hook") + ($args | ForEach-Object { Quote-Arg ([string]$_) })

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $PY
$psi.Arguments = $argList -join " "
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $false

$proc = [System.Diagnostics.Process]::Start($psi)
try {
    $inStream = [Console]::OpenStandardInput()
    $inStream.CopyTo($proc.StandardInput.BaseStream)
} catch {}
$proc.StandardInput.BaseStream.Close()
$outStream = [Console]::OpenStandardOutput()
$proc.StandardOutput.BaseStream.CopyTo($outStream)
$outStream.Flush()
$proc.WaitForExit()
exit $proc.ExitCode
