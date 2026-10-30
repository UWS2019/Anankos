@echo off
REM ===================================================================
REM  Anankos Sim Lab - one-click launcher (Windows)
REM  Starts the internal simulation server on an uncommon high port,
REM  bound to all interfaces so your Tailscale device can reach it.
REM ===================================================================
setlocal

set ANANKOS_HOST=0.0.0.0
set ANANKOS_PORT=47811

cd /d "%~dp0"

echo(
echo  ============================================
echo   ANANKOS SIM LAB  (training simulation only)
echo  ============================================
echo(

REM --- pick a python ---
where py >nul 2>nul && (set PY=py) || (set PY=python)

REM --- first-run dependency install ---
if not exist ".venv\" (
    echo [setup] creating virtual environment...
    %PY% -m venv .venv
    call ".venv\Scripts\activate.bat"
    echo [setup] installing dependencies...
    python -m pip install --quiet --upgrade pip
    python -m pip install --quiet -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

REM --- show how to reach it ---
echo(
echo  Local:     http://127.0.0.1:%ANANKOS_PORT%
for /f "tokens=2 delims=:" %%a in ('"tailscale ip -4 2>nul"') do set TSIP=%%a
tailscale ip -4 >nul 2>nul && (
    for /f %%i in ('tailscale ip -4 2^>nul') do echo  Tailscale: http://%%i:%ANANKOS_PORT%   ^(open this on your phone^)
) || (
    echo  Tailscale: not detected. Install + 'tailscale up' to reach this from your phone.
)
echo(

REM --- open a browser locally and launch the server ---
start "" "http://127.0.0.1:%ANANKOS_PORT%"
python -m simlab

endlocal
