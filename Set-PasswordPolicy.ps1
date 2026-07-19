[CmdletBinding(SupportsShouldProcess)]
param([string]$ConfigPath = "$PSScriptRoot\config\config.json", [switch]$Rollback)
Import-Module ActiveDirectory -EA Stop
$cfg = Get-Content $ConfigPath -Raw | ConvertFrom-Json
$bd = "$PSScriptRoot\backup"; New-Item -ItemType Directory -Force -Path $bd | Out-Null
$bp = "$bd\policy-$(Get-Date -f yyyyMMdd-HHmmss).json"
(Get-ADDefaultDomainPasswordPolicy) | ConvertTo-Json | Out-File $bp -Encoding UTF8
Write-Host "[BACKUP] $bp" -ForegroundColor Cyan
if ($Rollback) { Write-Host "Rollback: restore from $bd manually" -ForegroundColor Yellow; return }
$maxAge = New-TimeSpan -Days $cfg.Policy.MaxPasswordAgeDays
if ($PSCmdlet.ShouldProcess("Domain","MaxAge=$($cfg.Policy.MaxPasswordAgeDays)d")) {
    Set-ADDefaultDomainPasswordPolicy -Identity (Get-ADDomain).DistinguishedName -MaxPasswordAge $maxAge -MinPasswordLength 12 -ComplexityEnabled $true -PasswordHistoryCount 12 -LockoutThreshold 5 -LockoutDuration (New-TimeSpan -Minutes 30) -LockoutObservationWindow (New-TimeSpan -Minutes 30)
    Write-Host "[OK] Policy updated" -ForegroundColor Green
}
$users = Get-ADUser -SearchBase $cfg.Scope.TargetOU -Filter "PasswordNeverExpires -eq `$true -and Enabled -eq `$true" -Properties PasswordNeverExpires
$ex = @($cfg.Scope.ExcludedUsers)
foreach ($u in $users) {
    if ($ex -contains $u.SamAccountName) { continue }
    if ($PSCmdlet.ShouldProcess($u.SamAccountName,"Enable expiry")) {
        Set-ADUser -Identity $u -PasswordNeverExpires $false
        Write-Host "[FIX] $($u.SamAccountName)" -ForegroundColor Green
    }
}