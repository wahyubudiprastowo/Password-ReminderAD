@echo off
setlocal

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo This script must be run from an elevated Command Prompt or Run as Administrator PowerShell.
  echo.
  echo Example:
  echo   1. Right-click PowerShell
  echo   2. Choose "Run as Administrator"
  echo   3. Run:
  echo      .\PCE-LDAPS-Prepare.bat dc01.example.com
  exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%PCE-LDAPS-Prepare.ps1"

if "%~1"=="" (
  echo Usage:
  echo   PCE-LDAPS-Prepare.bat dc01.domain.local
  echo.
  echo Example:
  echo   PCE-LDAPS-Prepare.bat dc01.example.com
  echo.
  echo After CA issues the certificate:
  echo   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -ServerFqdn dc01.domain.local -AcceptIssuedCertificate -IssuedCertPath C:\temp\dc01.cer
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -ServerFqdn "%~1"

endlocal
