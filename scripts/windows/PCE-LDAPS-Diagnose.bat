@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PS_SCRIPT=%SCRIPT_DIR%PCE-LDAPS-Diagnose.ps1"

if "%~1"=="" (
  echo Usage:
  echo   PCE-LDAPS-Diagnose.bat dc01.domain.local [bindUser] [bindPassword] [searchBase]
  echo.
  echo Example:
  echo   PCE-LDAPS-Diagnose.bat dc01.example.com svc_pce@example.com MySecret123 "DC=example,DC=com"
  echo.
  echo Optional live reset test:
  echo   powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Server dc01.domain.local -BindUser svc@domain.local -BindPassword Secret123 -SearchBase "DC=domain,DC=local" -AttemptPasswordReset -TestUserIdentity user@domain.local -TemporaryPassword TempPass123!
  exit /b 1
)

set "SERVER=%~1"
set "BINDUSER=%~2"
set "BINDPASSWORD=%~3"
set "SEARCHBASE=%~4"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PS_SCRIPT%" -Server "%SERVER%" -BindUser "%BINDUSER%" -BindPassword "%BINDPASSWORD%" -SearchBase "%SEARCHBASE%"

endlocal
