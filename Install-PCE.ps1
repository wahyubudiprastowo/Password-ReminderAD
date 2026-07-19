param(
    [string]$InstallPath = "C:\ProgramData\PCE",
    [string]$RunAs = "$env:USERDOMAIN\svc_pce",
    [string]$Time = "08:00",
    [switch]$SkipSecretVaultSetup
)

$ErrorActionPreference = "Stop"

function Test-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

if (-not (Test-Admin)) {
    throw "Run this installer from an elevated PowerShell session."
}

New-Item -ItemType Directory -Force -Path $InstallPath | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallPath "data") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallPath "logs") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $InstallPath "config") | Out-Null

if ($PSScriptRoot -ne $InstallPath) {
    Copy-Item "$PSScriptRoot\*" $InstallPath -Recurse -Force -Exclude "data","logs"
}

$exampleConfig = Join-Path $InstallPath "config\config.example.json"
$runtimeConfig = Join-Path $InstallPath "config\config.json"
$exampleEnv = Join-Path $InstallPath ".env.example"
$runtimeEnv = Join-Path $InstallPath ".env"
if ((Test-Path -LiteralPath $exampleConfig) -and -not (Test-Path -LiteralPath $runtimeConfig)) {
    Copy-Item $exampleConfig $runtimeConfig -Force
    Write-Host "[OK] Created config.json from config.example.json" -ForegroundColor Green
}
if ((Test-Path -LiteralPath $exampleEnv) -and -not (Test-Path -LiteralPath $runtimeEnv)) {
    Copy-Item $exampleEnv $runtimeEnv -Force
    Write-Host "[OK] Created .env from .env.example" -ForegroundColor Green
}

if (-not $SkipSecretVaultSetup) {
    Write-Host "SMTP credential (optional, used if your Windows runner still depends on SecretVault):" -ForegroundColor Cyan
    $smtpCred = Get-Credential
    try {
        Import-Module Microsoft.PowerShell.SecretManagement -EA Stop
        if (-not (Get-SecretVault -Name PCEVault -EA SilentlyContinue)) {
            Register-SecretVault -Name PCEVault -ModuleName Microsoft.PowerShell.SecretStore -DefaultVault
        }
        Set-Secret -Name PCE-SmtpCred -Secret $smtpCred
        Write-Host "[OK] SMTP credential stored in SecretVault as PCE-SmtpCred" -ForegroundColor Green
    } catch {
        Write-Warning "SecretVault setup skipped or failed: $($_.Exception.Message)"
    }
}

$taskCredential = Get-Credential -UserName $RunAs -Message "Enter the Windows account credential that should run the scheduled task."
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$InstallPath\Invoke-PCE.ps1`""
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable

Register-ScheduledTask `
    -TaskName "Password-Compliance-Enforcer" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User $taskCredential.UserName `
    -Password ($taskCredential.GetNetworkCredential().Password) `
    -RunLevel Highest `
    -Force | Out-Null

Write-Host "[OK] Installed. Daily $Time as $($taskCredential.UserName)" -ForegroundColor Green
Write-Host "[INFO] Config path: $runtimeConfig" -ForegroundColor Cyan
Write-Host "[INFO] Env path: $runtimeEnv" -ForegroundColor Cyan
Write-Host "[INFO] Store AD/M365/SMTP/API token secrets in .env so config.json can stay non-sensitive." -ForegroundColor Cyan
Write-Host "TEST: powershell -File $InstallPath\Invoke-PCE.ps1 -WhatIf" -ForegroundColor Yellow
