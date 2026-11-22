@echo off
rem SessionCost launcher for Windows. It finds a Python 3.10+ (python, py, python3, the one uv manages), fetches one
rem through uv when there is none (into uv's own folder; nothing else changes), then runs SessionCost with the
rem arguments given; "run.cmd guard ..." runs the guard. One command, so an agent never has to hunt for Python.
setlocal EnableExtensions DisableDelayedExpansion
set "ROOT=%~dp0"
set "LB=%USERPROFILE%\.local\bin"
set "PY="
for %%P in (python py python3) do if not defined PY call :try "%%P"
if not defined PY for %%F in ("%LB%\python3*.exe") do if not defined PY call :try "%%~fF"
if not defined PY call :uvfind
if not defined PY if not defined SESSIONCOST_NO_FETCH call :fetch
if not defined PY goto nopy
rem -S: SessionCost needs only Python's own library; add-ons that load at every start can take seconds
%PY% -S "%ROOT%run.py" %*
exit /b

:try
"%~1" -S -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if not errorlevel 1 set PY="%~1"
exit /b 0

:uvfind
set "UV="
where uv >nul 2>&1 && set "UV=uv"
if exist "%LB%\uv.exe" set "UV=%LB%\uv.exe"
if not defined UV exit /b 0
rem uv finds its newest Python; :try then checks it is 3.10+ (cmd cannot pass ">=3.10" through for /f)
for /f "usebackq delims=" %%E in (`call "%UV%" python find --no-project 2^>nul`) do if not defined PY call :try "%%E"
exit /b 0

:fetch
echo SessionCost: no Python 3.10+ on this computer. Fetching one through uv, into its own folder; nothing else changes.
set "UV="
where uv >nul 2>&1 && set "UV=uv"
if exist "%LB%\uv.exe" set "UV=%LB%\uv.exe"
if not defined UV powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex" >nul 2>&1
if not defined UV if exist "%LB%\uv.exe" set "UV=%LB%\uv.exe"
if not defined UV exit /b 0
"%UV%" python install 3.12 >nul 2>&1
call :uvfind
if defined PY echo SessionCost: fetched Python 3.12 through uv.
exit /b 0

:nopy
echo SessionCost needs Python 3.10 or newer. None was found here, and fetching one through uv did not work
echo (a sandbox may block downloads or writing to the user's folder).
echo Agent: ask the user once to allow this same command outside the sandbox, then run it again. Try nothing else.
echo If it fails again, the user types these two lines once in PowerShell, then asks again:
echo   powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
echo   ^& "$HOME\.local\bin\uv.exe" python install 3.12
exit /b 3
