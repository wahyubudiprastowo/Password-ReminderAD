function Get-PCEScopedUsers {
    param($Config)
    $params = @{
        SearchBase = $Config.Scope.TargetOU
        Filter     = if ($Config.Scope.IncludeDisabledAccounts) { "*" } else { "Enabled -eq `$true" }
        Properties = "PasswordLastSet","PasswordNeverExpires","EmailAddress","UserPrincipalName",
                     "DisplayName","Manager","msDS-UserPasswordExpiryTimeComputed","LockedOut"
    }
    $all = Get-ADUser @params
    $exUsers = @($Config.Scope.ExcludedUsers)
    $exGroupMembers = @()
    foreach ($g in $Config.Scope.ExcludedGroups) {
        try {
            $exGroupMembers += (Get-ADGroupMember -Identity $g -Recursive -ErrorAction Stop |
                                Select-Object -ExpandProperty SamAccountName)
        } catch {}
    }
    $all | Where-Object {
        $_.SamAccountName -notin $exUsers -and
        $_.SamAccountName -notin $exGroupMembers -and
        -not $_.PasswordNeverExpires
    }
}

function Get-PCEUserPasswordInfo {
    param($User, $Config)
    $expiryFileTime = $User."msDS-UserPasswordExpiryTimeComputed"
    if ($expiryFileTime -and $expiryFileTime -gt 0 -and $expiryFileTime -lt :MaxValue) {
        try { $expiryDate = :FromFileTime($expiryFileTime) }
        catch { $expiryDate = $User.PasswordLastSet.AddDays($Config.Policy.MaxPasswordAgeDays) }
    } else {
        $expiryDate = if ($User.PasswordLastSet) {
            $User.PasswordLastSet.AddDays($Config.Policy.MaxPasswordAgeDays)
        } else { (Get-Date).AddDays($Config.Policy.MaxPasswordAgeDays) }
    }
    [pscustomobject]@{
        SamAccountName     = $User.SamAccountName
        UserPrincipalName  = $User.UserPrincipalName
        DisplayName        = $User.DisplayName
        Email              = $User.EmailAddress
        PasswordLastSet    = $User.PasswordLastSet
        PasswordExpiryDate = $expiryDate
        DaysUntilExpiry    = :Floor(($expiryDate - (Get-Date)).TotalDays)
        IsLocked           = [bool]$User.LockedOut
    }
}

Export-ModuleMember -Function *