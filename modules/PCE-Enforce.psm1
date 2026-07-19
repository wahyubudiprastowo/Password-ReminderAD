function Set-PCEForcePasswordChange {
    [CmdletBinding(SupportsShouldProcess)]
    param($User, $Config)
    if ($PSCmdlet.ShouldProcess($User.SamAccountName, "ChangePasswordAtLogon")) {
        Set-ADUser -Identity $User -ChangePasswordAtLogon $true
        Write-Host "[FORCE] $($User.SamAccountName)" -ForegroundColor Yellow
    }
}
function Disable-PCEAccount {
    [CmdletBinding(SupportsShouldProcess)]
    param($User, $Config)
    if ($PSCmdlet.ShouldProcess($User.SamAccountName, "Disable")) {
        Disable-ADAccount -Identity $User
        Set-ADUser -Identity $User -Description "PCE-LOCKED $(Get-Date -Format 'yyyy-MM-dd')"
        Write-Host "[LOCK] $($User.SamAccountName)" -ForegroundColor Red
    }
}
function Enable-PCEAccount {
    [CmdletBinding(SupportsShouldProcess)]
    param($User, $Config)
    if ($PSCmdlet.ShouldProcess($User.SamAccountName, "Enable+Unlock")) {
        Unlock-ADAccount -Identity $User -ErrorAction SilentlyContinue
        Enable-ADAccount -Identity $User
        Set-ADUser -Identity $User -ChangePasswordAtLogon $true
        Write-Host "[UNLOCK] $($User.SamAccountName)" -ForegroundColor Green
    }
}
Export-ModuleMember -Function *