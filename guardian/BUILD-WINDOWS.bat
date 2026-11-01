@echo off
REM ===========================================================
REM  Anankos Guardian - build a real installer (Windows)
REM  Double-click this to create a double-click Setup .exe.
REM ===========================================================
setlocal
cd /d "%~dp0"
title Anankos Guardian - Build Installer

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo  Node.js is not installed yet.
  echo  1^) Go to https://nodejs.org
  echo  2^) Download the button that says "LTS"
  echo  3^) Run that installer, then double-click this file again.
  echo.
  pause
  exit /b 1
)

echo.
echo  Step 1 of 2: downloading components (first time can take a few minutes)...
echo.
call npm install
if errorlevel 1 goto :error

echo.
echo  Step 2 of 2: building your installer...
echo.
call npm run dist:win
if errorlevel 1 goto :error

echo.
echo  ============================================================
echo   Done! Opening the folder with your installer.
echo   Look for the file ending in:  Setup 1.0.0.exe
echo   Double-click that file to install Anankos Guardian.
echo  ============================================================
echo.
start "" "%~dp0release"
pause
exit /b 0

:error
echo.
echo  Something went wrong. Copy the messages above and send them to support.
pause
exit /b 1
