param(
    [Parameter(Mandatory = $true)]
    [string]$Identity,

    [string]$NewPassword,

    [switch]$GenerateTemporaryPassword,

    [int]$PasswordLength = 18,

    [switch]$ForceChangeAtLogon,

    [switch]$UnlockIfLocked,

    [switch]$EnableIfDisabled,

    [string]$LogPath = "C:\ProgramData\PCE\password-reset.log"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Log {
    param([string]$Message)
    $dir = Split-Path -Parent $LogPath
    if ($dir -and -not (Test-Path -LiteralPath $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message)
}

function New-StrongPassword {
    param([int]$Length = 18)
    $upper = "ABCDEFGHJKLMNPQRSTUVWXYZ".ToCharArray()
    $lower = "abcdefghijkmnopqrstuvwxyz".ToCharArray()
    $digits = "23456789".ToCharArray()
    $symbols = "!@#$%^&*_-+=?".ToCharArray()
    $all = @($upper + $lower + $digits + $symbols)
    if ($Length -lt 12) { $Length = 12 }

    $chars = New-Object System.Collections.Generic.List[char]
    $chars.Add(($upper | Get-Random))
    $chars.Add(($lower | Get-Random))
    $chars.Add(($digits | Get-Random))
    $chars.Add(($symbols | Get-Random))

    for ($i = $chars.Count; $i -lt $Length; $i++) {
        $chars.Add(($all | Get-Random))
    }

    $shuffled = $chars | Sort-Object { Get-Random }
    return -join $shuffled
}

try {
    Import-Module ActiveDirectory -ErrorAction Stop

    $user = Get-ADUser -LDAPFilter "(|(sAMAccountName=$Identity)(userPrincipalName=$Identity)(mail=$Identity))" `
        -Properties DisplayName, UserPrincipalName, mail, Enabled, LockedOut

    if (-not $user) {
        throw "User not found: $Identity"
    }

    if ($EnableIfDisabled -and -not $user.Enabled) {
        Enable-ADAccount -Identity $user.DistinguishedName
        Write-Log ("Enabled user {0}" -f $user.SamAccountName)
    }

    if ($UnlockIfLocked) {
        try {
            Unlock-ADAccount -Identity $user.DistinguishedName
            Write-Log ("Unlocked user {0}" -f $user.SamAccountName)
        } catch {
            Write-Log ("Unlock skipped/failed for {0}: {1}" -f $user.SamAccountName, $_.Exception.Message)
        }
    }

    $resolvedPassword = $NewPassword
    if ($GenerateTemporaryPassword -or [string]::IsNullOrWhiteSpace($resolvedPassword)) {
        $resolvedPassword = New-StrongPassword -Length $PasswordLength
    }

    $securePassword = ConvertTo-SecureString -String $resolvedPassword -AsPlainText -Force
    Set-ADAccountPassword -Identity $user.DistinguishedName -Reset -NewPassword $securePassword
    Write-Log ("Password reset for {0}" -f $user.SamAccountName)

    if ($ForceChangeAtLogon) {
        Set-ADUser -Identity $user.DistinguishedName -ChangePasswordAtLogon $true
        Write-Log ("ChangePasswordAtLogon enabled for {0}" -f $user.SamAccountName)
    }

    $result = [ordered]@{
        ok = $true
        identity = $Identity
        sam = $user.SamAccountName
        upn = $user.UserPrincipalName
        display_name = $user.DisplayName
        email = $user.mail
        temporary_password = $resolvedPassword
        generated_password = [bool]($GenerateTemporaryPassword -or [string]::IsNullOrWhiteSpace($NewPassword))
        force_change_at_logon = [bool]$ForceChangeAtLogon
        unlock_if_locked = [bool]$UnlockIfLocked
        enable_if_disabled = [bool]$EnableIfDisabled
        changed_at = (Get-Date).ToString("o")
    }

    $result | ConvertTo-Json -Depth 3
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Log ("FAILED for {0}: {1}" -f $Identity, $message)
    [ordered]@{
        ok = $false
        identity = $Identity
        error = $message
        changed_at = (Get-Date).ToString("o")
    } | ConvertTo-Json -Depth 3
    exit 1
}
