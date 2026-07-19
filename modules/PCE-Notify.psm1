function Get-PCESmtpCredential {
    param($Config)
    Import-Module Microsoft.PowerShell.SecretManagement -ErrorAction SilentlyContinue
    try { return Get-Secret -Name $Config.Notification.CredentialVaultName }
    catch { throw "SMTP credential not found. Run: Set-Secret -Name $($Config.Notification.CredentialVaultName) -Secret (Get-Credential)" }
}
function Send-PCEUserWarning {
    [CmdletBinding(SupportsShouldProcess)]
    param($User, [int]$DaysLeft, $Config)
    if (-not $User.EmailAddress) { return }
    $tpl = Get-Content (Join-Path $PSScriptRoot "..\templates\user-warning.html") -Raw
    $body = $tpl.Replace("{{DisplayName}}",$User.DisplayName).Replace("{{DaysLeft}}",[string]$DaysLeft).Replace("{{ExpiryDate}}",(Get-Date).AddDays($DaysLeft).ToString("dd MMM yyyy")).Replace("{{ResetUrl}}","https://passwordreset.microsoftonline.com").Replace("{{HelpdeskEmail}}","helpdesk@example.com")
    $p = @{
        SmtpServer=$Config.Notification.SmtpServer; Port=$Config.Notification.SmtpPort
        UseSsl=$Config.Notification.UseSsl
        From="$($Config.Notification.FromDisplayName) <$($Config.Notification.FromAddress)>"
        To=$User.EmailAddress
        Subject="[ACTION] Password expire dalam $DaysLeft hari"
        Body=$body; BodyAsHtml=$true
        Credential=(Get-PCESmtpCredential -Config $Config)
    }
    if ($PSCmdlet.ShouldProcess($User.EmailAddress,"Warning")) {
        Send-MailMessage @p
        Write-Host "[MAIL] -> $($User.EmailAddress)" -ForegroundColor Cyan
    }
}
function Send-PCEUserLockedNotice {
    [CmdletBinding(SupportsShouldProcess)]
    param($User, $Config)
    if (-not $User.EmailAddress) { return }
    $tpl = Get-Content (Join-Path $PSScriptRoot "..\templates\user-locked.html") -Raw
    $body = $tpl.Replace("{{DisplayName}}",$User.DisplayName).Replace("{{HelpdeskEmail}}","helpdesk@example.com").Replace("{{HelpdeskPhone}}","+62-21-XXXXXXX")
    $p = @{
        SmtpServer=$Config.Notification.SmtpServer; Port=$Config.Notification.SmtpPort
        UseSsl=$Config.Notification.UseSsl
        From="$($Config.Notification.FromDisplayName) <$($Config.Notification.FromAddress)>"
        To=$User.EmailAddress; Cc=$Config.Notification.AdminRecipients
        Subject="[LOCKED] Password expired - Hubungi Helpdesk"
        Body=$body; BodyAsHtml=$true
        Credential=(Get-PCESmtpCredential -Config $Config)
    }
    if ($PSCmdlet.ShouldProcess($User.EmailAddress,"Locked")) { Send-MailMessage @p }
}
function Send-PCEAdminReport {
    param($Stats, $Actions, $Config, $RunId)
    $tpl = Get-Content (Join-Path $PSScriptRoot "..\templates\admin-report.html") -Raw
    $actHtml = ($Actions | ConvertTo-Html -Fragment -Property User,Action,DaysLeft,Email) -join "`n"
    $body = $tpl.Replace("{{RunId}}",$RunId).Replace("{{Date}}",(Get-Date -Format "dd MMM yyyy HH:mm")).Replace("{{Total}}",[string]$Stats.Total).Replace("{{Compliant}}",[string]$Stats.Compliant).Replace("{{Warned}}",[string]$Stats.Warned).Replace("{{ForcedChange}}",[string]$Stats.ForcedChange).Replace("{{Disabled}}",[string]$Stats.Disabled).Replace("{{Errors}}",[string]$Stats.Errors).Replace("{{ActionsTable}}",$actHtml)
    $atts = @()
    if ($Config.Report.AttachCsv -and $Actions.Count -gt 0) {
        $csv = Join-Path $Config.Logging.Path "pce-actions-$RunId.csv"
        $Actions | Export-Csv $csv -NoTypeInformation -Encoding UTF8
        $atts += $csv
    }
    $p = @{
        SmtpServer=$Config.Notification.SmtpServer; Port=$Config.Notification.SmtpPort
        UseSsl=$Config.Notification.UseSsl
        From="$($Config.Notification.FromDisplayName) <$($Config.Notification.FromAddress)>"
        To=$Config.Notification.AdminRecipients
        Subject="[PCE] W:$($Stats.Warned) F:$($Stats.ForcedChange) L:$($Stats.Disabled) - $RunId"
        Body=$body; BodyAsHtml=$true
        Credential=(Get-PCESmtpCredential -Config $Config)
    }
    if ($atts.Count -gt 0) { $p.Attachments = $atts }
    Send-MailMessage @p
    Write-Host "[REPORT] Sent" -ForegroundColor Green
}
Export-ModuleMember -Function *
