@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%PCE-PasswordReset.ps1"

if "%~1"=="" (
  echo Usage:
  echo   PCE-PasswordReset.bat user@domain.com [TempPassword]
  echo.
  echo Examples:
  echo   PCE-PasswordReset.bat user@example.com
  echo   PCE-PasswordReset.bat user@example.com TempPass123!
  exit /b 1
)

set "IDENTITY=%~1"
set "PASSWORD=%~2"

if "%PASSWORD%"=="" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Identity "%IDENTITY%" -GenerateTemporaryPassword -ForceChangeAtLogon -UnlockIfLocked -EnableIfDisabled
) else (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Identity "%IDENTITY%" -NewPassword "%PASSWORD%" -ForceChangeAtLogon -UnlockIfLocked -EnableIfDisabled
)

endlocal
