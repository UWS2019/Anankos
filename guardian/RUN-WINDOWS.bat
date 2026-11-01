@echo off
REM ===========================================================
REM  Anankos Guardian - quick run (Windows)
REM  Double-click this to start the app without installing it.
REM ===========================================================
setlocal
cd /d "%~dp0"
title Anankos Guardian

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

if not exist node_modules (
  echo.
  echo  First time setup - downloading components.
  echo  This can take a few minutes. Please wait...
  echo.
  call npm install
  if errorlevel 1 (
    echo.
    echo  Setup failed. Copy the text above and send it to support.
    pause
    exit /b 1
  )
)

echo.
echo  Starting Anankos Guardian...
echo  (Leave this black window open while you use the app.)
echo.
call npm start
pause
