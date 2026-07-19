function Push-PCEDashboard {
    param($Stats, $Actions, $UsersSnapshot, $Config, $RunId, [DateTime]$StartedAt, [DateTime]$FinishedAt)
    if (-not $Config.Dashboard.Enabled) { return }
    $url = "$($Config.Dashboard.BaseUrl.TrimEnd('/'))/api/ingest"
    $payload = @{
        run_id=$RunId; started_at=$StartedAt.ToString("o"); finished_at=$FinishedAt.ToString("o")
        whatif=[bool]$Config.Safety.WhatIfMode; server=$env:COMPUTERNAME
        stats=$Stats
        actions=@($Actions | ForEach-Object { @{ User=$_.User; Action=$_.Action; DaysLeft=$_.DaysLeft; Email=$_.Email } })
        users_snapshot=@($UsersSnapshot | ForEach-Object {
            @{
                SamAccountName=$_.SamAccountName; UserPrincipalName=$_.UserPrincipalName
                DisplayName=$_.DisplayName; Email=$_.Email
                PasswordLastSet=if ($_.PasswordLastSet) { $_.PasswordLastSet.ToString("o") } else { $null }
                PasswordExpiryDate=if ($_.PasswordExpiryDate) { $_.PasswordExpiryDate.ToString("o") } else { $null }
                DaysUntilExpiry=$_.DaysUntilExpiry; IsLocked=$_.IsLocked
            }
        })
    }
    try {
        Invoke-RestMethod -Uri $url -Method POST -Body ($payload | ConvertTo-Json -Depth 10 -Compress) -Headers @{ "X-API-Token" = $Config.Dashboard.ApiToken } -ContentType "application/json" -TimeoutSec 30 | Out-Null
        Write-Host "[DASHBOARD] Pushed $RunId" -ForegroundColor Green
    } catch { Write-Warning "[DASHBOARD] $_" }
}
Export-ModuleMember -Function Push-PCEDashboard