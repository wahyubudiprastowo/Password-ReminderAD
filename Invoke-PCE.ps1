[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = "$PSScriptRoot\config\config.json", [switch]$WhatIf)

$ErrorActionPreference = "Stop"
$startedAt = Get-Date
Import-Module ActiveDirectory
Import-Module "$PSScriptRoot\modules\PCE-Core.psm1"      -Force
Import-Module "$PSScriptRoot\modules\PCE-Notify.psm1"    -Force
Import-Module "$PSScriptRoot\modules\PCE-Enforce.psm1"   -Force
Import-Module "$PSScriptRoot\modules\PCE-M365.psm1"      -Force
Import-Module "$PSScriptRoot\modules\PCE-Teams.psm1"     -Force
Import-Module "$PSScriptRoot\modules\PCE-Dashboard.psm1" -Force

$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
function Import-PceEnvFile {
    param([string]$EnvPath)
    if (-not (Test-Path -LiteralPath $EnvPath)) { return }
    foreach ($line in Get-Content -LiteralPath $EnvPath) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith("#")) { continue }
        $pair = $line -split "=", 2
        if ($pair.Count -ne 2) { continue }
        if (-not [string]::IsNullOrWhiteSpace($pair[0]) -and [string]::IsNullOrEmpty([Environment]::GetEnvironmentVariable($pair[0]))) {
            [Environment]::SetEnvironmentVariable($pair[0], $pair[1])
        }
    }
}
Import-PceEnvFile -EnvPath (Join-Path $PSScriptRoot ".env")
if (-not $cfg.ActiveDirectory.BindPassword -and $env:PCE_AD_BIND_PASSWORD) {
    $cfg.ActiveDirectory.BindPassword = $env:PCE_AD_BIND_PASSWORD
}
if (-not $cfg.M365.ClientSecret -and $env:PCE_M365_CLIENT_SECRET) {
    $cfg.M365.ClientSecret = $env:PCE_M365_CLIENT_SECRET
}
if (-not $cfg.Notification.Password -and $env:PCE_NOTIFICATION_SMTP_PASSWORD) {
    $cfg.Notification.Password = $env:PCE_NOTIFICATION_SMTP_PASSWORD
}
if (-not $cfg.Dashboard.ApiToken -and $env:PCE_API_TOKEN) {
    $cfg.Dashboard.ApiToken = $env:PCE_API_TOKEN
}
if ($WhatIf) { $cfg.Safety.WhatIfMode = $true }

$runId = Get-Date -Format "yyyyMMdd-HHmmss"
New-Item -ItemType Directory -Force -Path $cfg.Logging.Path | Out-Null
Start-Transcript -Path (Join-Path $cfg.Logging.Path "pce-$runId.log") -Append | Out-Null

Write-Host "PCE Run: $runId (WhatIf=$($cfg.Safety.WhatIfMode))" -ForegroundColor Cyan
$users = Get-PCEScopedUsers -Config $cfg
Write-Host "Scoped: $($users.Count)"

$stats = [ordered]@{ Total=$users.Count; Compliant=0; Warned=0; ForcedChange=0; Disabled=0; Errors=0 }
$actions = New-Object System.Collections.Generic.List[object]
$usersInfo = New-Object System.Collections.Generic.List[object]

if ($cfg.M365.Enabled) { try { Connect-PCEGraph -Config $cfg } catch { Write-Warning $_ } }

foreach ($u in $users) {
    try {
        $info = Get-PCEUserPasswordInfo -User $u -Config $cfg
        $usersInfo.Add($info)
        $d = $info.DaysUntilExpiry
        $maxW = ($cfg.Policy.WarningDays | Measure-Object -Maximum).Maximum

        if ($d -gt $maxW) { $stats.Compliant++; continue }

        if ($d -gt 0 -and ($cfg.Policy.WarningDays -contains $d)) {
            Send-PCEUserWarning -User $u -DaysLeft $d -Config $cfg -WhatIf:$cfg.Safety.WhatIfMode
            if ($cfg.Teams.SendUserDMs) { Send-PCETeamsUserWarning -User $u -DaysLeft $d -Config $cfg }
            $stats.Warned++
            $actions.Add([pscustomobject]@{ User=$u.SamAccountName; Action="Warned"; DaysLeft=$d; Email=$u.EmailAddress })
        }
        if ($d -le $cfg.Policy.ForceChangeAtLogonOnDay -and $d -gt -$cfg.Policy.GracePeriodDaysAfterExpiry) {
            Set-PCEForcePasswordChange -User $u -Config $cfg -WhatIf:$cfg.Safety.WhatIfMode
            $stats.ForcedChange++
            $actions.Add([pscustomobject]@{ User=$u.SamAccountName; Action="ForcedChange"; DaysLeft=$d; Email=$u.EmailAddress })
        }
        if ($d -le -$cfg.Policy.GracePeriodDaysAfterExpiry) {
            if ($stats.Disabled -ge $cfg.Safety.MaxDisablesPerRun) {
                Send-PCETeamsSafetyAlert -AlertType "MassLockoutTripped" -Message "Cap reached ($($cfg.Safety.MaxDisablesPerRun))" -Context @{ RunId=$runId } -Config $cfg
                break
            }
            Disable-PCEAccount -User $u -Config $cfg -WhatIf:$cfg.Safety.WhatIfMode
            if ($cfg.M365.Enabled -and $cfg.M365.RevokeSessionsOnLock) {
                Revoke-PCEM365Sessions -Upn $u.UserPrincipalName -WhatIf:$cfg.Safety.WhatIfMode
            }
            Send-PCEUserLockedNotice -User $u -Config $cfg -WhatIf:$cfg.Safety.WhatIfMode
            if ($cfg.Teams.SendUserDMs) { Send-PCETeamsUserLocked -User $u -Config $cfg }
            $stats.Disabled++
            $actions.Add([pscustomobject]@{ User=$u.SamAccountName; Action="Disabled"; DaysLeft=$d; Email=$u.EmailAddress })
        }
    } catch {
        $stats.Errors++
        Write-Warning "[ERR] $($u.SamAccountName): $_"
    }
}

if ($cfg.Report.SendDailyDigest) {
    try { Send-PCEAdminReport -Stats $stats -Actions $actions -Config $cfg -RunId $runId } catch { Write-Warning $_ }
}
if ($cfg.Teams.Enabled -and $cfg.Teams.PostAdminDigest) {
    try { Send-PCETeamsAdminDigest -Stats $stats -Actions $actions -Config $cfg -RunId $runId } catch { Write-Warning $_ }
}
try {
    Push-PCEDashboard -Stats $stats -Actions $actions -UsersSnapshot $usersInfo -Config $cfg -RunId $runId -StartedAt $startedAt -FinishedAt (Get-Date)
} catch { Write-Warning $_ }

Write-Host "=== SUMMARY ===" -ForegroundColor Green
$stats.GetEnumerator() | ForEach-Object { "{0,-14}: {1}" -f $_.Key, $_.Value }
Stop-Transcript | Out-Null
