@echo off
setlocal

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo This script must be run from an elevated Command Prompt or Run as Administrator PowerShell.
  exit /b 1
)

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%PCE-LDAPS-Repair.ps1"

if "%~1"=="" (
  echo Usage:
  echo   PCE-LDAPS-Repair.bat dc01.domain.local
  echo.
  echo Example audit + CSR:
  echo   PCE-LDAPS-Repair.bat dc01.example.com
  echo.
  echo Example after CA issues certificate:
  echo   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -ServerFqdn dc01.domain.local -IssuedCertPath C:\temp\dc01.cer -PromoteToNtdsStore
  exit /b 1
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -ServerFqdn "%~1"

endlocal
