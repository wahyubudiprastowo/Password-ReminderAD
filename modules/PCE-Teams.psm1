$script:TemplatesPath = Join-Path $PSScriptRoot "..\templates"

function Invoke-PCETeamsWebhook {
    param([string]$WebhookUrl, [hashtable]$Payload, [int]$MaxRetries=3, [int]$RetryDelaySeconds=5, [int]$TimeoutSeconds=30, [string]$Context="TeamsPost")
    $json = $Payload | ConvertTo-Json -Depth 20 -Compress
    for ($i=1; $i -le $MaxRetries; $i++) {
        try {
            Invoke-RestMethod -Uri $WebhookUrl -Method POST -Body $json -ContentType "application/json" -TimeoutSec $TimeoutSeconds -ErrorAction Stop | Out-Null
            Write-Host "[TEAMS] OK $Context" -ForegroundColor Green
            return
        } catch {
            $s = try { $_.Exception.Response.StatusCode.value__ } catch { 0 }
            if ($s -eq 429 -or $s -ge 500) { Start-Sleep -Seconds ($RetryDelaySeconds*$i); continue }
            Write-Warning "[TEAMS] $s $_"; return
        }
    }
}

function Get-PCETeamsCard {
    param([string]$TemplateName, [hashtable]$Tokens)
    $raw = Get-Content (Join-Path $script:TemplatesPath $TemplateName) -Raw -Encoding UTF8
    foreach ($k in $Tokens.Keys) {
        $v = if ($null -eq $Tokens[$k]) { "" } else { [string]$Tokens[$k] }
        $raw = $raw.Replace("{{$k}}", $v)
    }
    return ($raw | ConvertFrom-Json -Depth 20 -AsHashtable)
}

function Send-PCETeamsAdminDigest {
    param($Stats, $Actions, $Config, $RunId)
    if (-not $Config.Teams.Enabled -or -not $Config.Teams.PostAdminDigest) { return }
    $url = $Config.Teams.WebhookUrls.AdminDigest
    if (:IsNullOrWhiteSpace($url)) { return }
    $preview = ($Actions | Select-Object -First 10 | ForEach-Object { "- $($_.User) -> $($_.Action) ($($_.DaysLeft)d)" }) -join "`n"
    $color = if ($Stats.Disabled -gt 0) { "attention" } elseif ($Stats.Warned -gt 0) { "warning" } else { "good" }
    $mText = ""
    if ($Config.Teams.MentionAdminsOnLock -and $Stats.Disabled -gt 0) {
        foreach ($upn in $Config.Teams.AdminUpnsToMention) { $mText += "<at>$upn</at> " }
    }
    $tk = @{
        RunId=$RunId; Date=(Get-Date -Format "dd MMM yyyy HH:mm"); Severity=$color
        Total=$Stats.Total; Compliant=$Stats.Compliant; Warned=$Stats.Warned
        ForcedChange=$Stats.ForcedChange; Disabled=$Stats.Disabled; Errors=$Stats.Errors
        ActionsPreview=$preview; MentionText=$mText
    }
    $card = Get-PCETeamsCard -TemplateName "teams-admin-digest.json" -Tokens $tk
    Invoke-PCETeamsWebhook -WebhookUrl $url -Payload $card -Context "Digest-$RunId" -MaxRetries $Config.Teams.MaxRetries -RetryDelaySeconds $Config.Teams.RetryDelaySeconds -TimeoutSeconds $Config.Teams.TimeoutSeconds
}

function Send-PCETeamsSafetyAlert {
    param([string]$AlertType, [string]$Message, [hashtable]$Context=@{}, $Config)
    if (-not $Config.Teams.Enabled -or -not $Config.Teams.PostSafetyAlerts) { return }
    $url = $Config.Teams.WebhookUrls.SecurityAlerts
    if (:IsNullOrWhiteSpace($url)) { return }
    $ctxL = ($Context.GetEnumerator() | ForEach-Object { "- $($_.Key): $($_.Value)" }) -join "`n"
    $mText = ""
    foreach ($upn in $Config.Teams.AdminUpnsToMention) { $mText += "<at>$upn</at> " }
    $tk = @{ AlertType=$AlertType; Message=$Message; Timestamp=(Get-Date -Format "yyyy-MM-dd HH:mm:ss"); ContextList=$ctxL; MentionText=$mText; Server=$env:COMPUTERNAME }
    $card = Get-PCETeamsCard -TemplateName "teams-safety-alert.json" -Tokens $tk
    Invoke-PCETeamsWebhook -WebhookUrl $url -Payload $card -Context "Alert-$AlertType"
}

function Send-PCETeamsUserWarning {
    param($User, [int]$DaysLeft, $Config)
    if (-not $Config.Teams.SendUserDMs) { return }
    $url = $Config.Teams.WebhookUrls.UserNotifications
    if (:IsNullOrWhiteSpace($url)) { return }
    $tk = @{ DisplayName=$User.DisplayName; Upn=$User.UserPrincipalName; DaysLeft=$DaysLeft; ExpiryDate=(Get-Date).AddDays($DaysLeft).ToString("dd MMM yyyy"); ResetUrl="https://passwordreset.microsoftonline.com"; HelpdeskEmail="helpdesk@example.com" }
    $card = Get-PCETeamsCard -TemplateName "teams-user-warning.json" -Tokens $tk
    Invoke-PCETeamsWebhook -WebhookUrl $url -Payload $card -Context "UserWarn-$($User.SamAccountName)"
}

function Send-PCETeamsUserLocked {
    param($User, $Config)
    if (-not $Config.Teams.SendUserDMs) { return }
    $url = $Config.Teams.WebhookUrls.UserNotifications
    if (:IsNullOrWhiteSpace($url)) { return }
    $tk = @{ DisplayName=$User.DisplayName; Upn=$User.UserPrincipalName; HelpdeskEmail="helpdesk@example.com"; HelpdeskPhone="+62-21-XXXXXXX" }
    $card = Get-PCETeamsCard -TemplateName "teams-user-locked.json" -Tokens $tk
    Invoke-PCETeamsWebhook -WebhookUrl $url -Payload $card -Context "UserLock-$($User.SamAccountName)"
}

Export-ModuleMember -Function *
