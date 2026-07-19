param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [int]$Port = 636,

    [string]$BindUser,

    [string]$BindPassword,

    [string]$SearchBase,

    [string]$TestUserIdentity,

    [string]$TemporaryPassword,

    [switch]$AttemptPasswordReset,

    [string]$LogPath = "C:\ProgramData\PCE\ldaps-diagnose.log"
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

function Get-ServerAuthCertificates {
    param([string]$DnsName)

    $serverAuthOid = "1.3.6.1.5.5.7.3.1"
    $items = New-Object System.Collections.ArrayList
    $store = New-Object System.Security.Cryptography.X509Certificates.X509Store("My", "LocalMachine")
    $store.Open([System.Security.Cryptography.X509Certificates.OpenFlags]::ReadOnly)

    try {
        foreach ($cert in $store.Certificates) {
            $hasServerAuth = $false

            foreach ($ext in $cert.Extensions) {
                if ($ext.Oid.Value -eq "2.5.29.37") {
                    $eku = New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension($ext, $false)
                    foreach ($oid in $eku.EnhancedKeyUsages) {
                        if ($oid.Value -eq $serverAuthOid) {
                            $hasServerAuth = $true
                            break
                        }
                    }
                }
                if ($hasServerAuth) { break }
            }

            if (-not $hasServerAuth) {
                continue
            }

            $dnsNames = @()
            try {
                $dnsNames = $cert.Extensions |
                    Where-Object { $_.Oid.FriendlyName -eq "Subject Alternative Name" } |
                    ForEach-Object { $_.Format($false) } |
                    ForEach-Object { $_ -split "," } |
                    ForEach-Object { $_.Trim() }
            } catch {}

            $nameMatches = $false
            if ([string]$cert.Subject -match [regex]::Escape($DnsName)) {
                $nameMatches = $true
            } else {
                foreach ($dns in $dnsNames) {
                    if ([string]$dns -match [regex]::Escape($DnsName)) {
                        $nameMatches = $true
                        break
                    }
                }
            }

            [void]$items.Add([PSCustomObject]@{
                subject = [string]$cert.Subject
                issuer = [string]$cert.Issuer
                thumbprint = [string]$cert.Thumbprint
                not_before = $cert.NotBefore.ToString("o")
                not_after = $cert.NotAfter.ToString("o")
                has_server_auth = [bool]$hasServerAuth
                matches_server_name = [bool]$nameMatches
            })
        }
    }
    finally {
        $store.Close()
    }

    return @($items.ToArray())
}

function Test-LdapsSocket {
    param(
        [string]$HostName,
        [int]$TcpPort
    )

    try {
        $result = Test-NetConnection -ComputerName $HostName -Port $TcpPort -WarningAction SilentlyContinue

        $remoteAddress = $null
        $sourceAddress = $null
        $interfaceAlias = $null

        try { if ($result.RemoteAddress) { $remoteAddress = $result.RemoteAddress.IPAddressToString } } catch {}
        try { if ($result.SourceAddress) { $sourceAddress = $result.SourceAddress.IPAddressToString } } catch {}
        try { $interfaceAlias = [string]$result.InterfaceAlias } catch {}

        return [PSCustomObject]@{
            ok = [bool]$result.TcpTestSucceeded
            remote_address = $remoteAddress
            source_address = $sourceAddress
            interface_alias = $interfaceAlias
        }
    }
    catch {
        return [PSCustomObject]@{
            ok = $false
            error = $_.Exception.Message
        }
    }
}

function Test-LdapsBind {
    param(
        [string]$HostName,
        [int]$TcpPort,
        [string]$Username,
        [string]$Password,
        [string]$BaseDn
    )

    Add-Type -AssemblyName System.DirectoryServices.Protocols

    $identifier = New-Object System.DirectoryServices.Protocols.LdapDirectoryIdentifier($HostName, $TcpPort, $false, $false)
    $connection = $null

    try {
        if ($Username) {
            $credential = New-Object System.Net.NetworkCredential($Username, $Password)
            $connection = New-Object System.DirectoryServices.Protocols.LdapConnection($identifier, $credential, [System.DirectoryServices.Protocols.AuthType]::Basic)
            $connection.AuthType = [System.DirectoryServices.Protocols.AuthType]::Basic
        } else {
            $connection = New-Object System.DirectoryServices.Protocols.LdapConnection($identifier)
            $connection.AuthType = [System.DirectoryServices.Protocols.AuthType]::Anonymous
        }

        $connection.SessionOptions.SecureSocketLayer = $true
        $connection.SessionOptions.ProtocolVersion = 3
        $connection.Timeout = [TimeSpan]::FromSeconds(15)

        $connection.Bind()

        $rootRequest = New-Object System.DirectoryServices.Protocols.SearchRequest(
            $null,
            "(objectClass=*)",
            [System.DirectoryServices.Protocols.SearchScope]::Base,
            @("defaultNamingContext", "dnsHostName")
        )
        $rootResponse = $connection.SendRequest($rootRequest)
        $rootEntry = $rootResponse.Entries[0]

        $searchBaseOk = $null
        if ($BaseDn) {
            $probe = New-Object System.DirectoryServices.Protocols.SearchRequest(
                $BaseDn,
                "(objectClass=*)",
                [System.DirectoryServices.Protocols.SearchScope]::Base,
                @("distinguishedName")
            )
            $null = $connection.SendRequest($probe)
            $searchBaseOk = $true
        }

        return [PSCustomObject]@{
            ok = $true
            default_naming_context = [string]$rootEntry.Attributes["defaultNamingContext"][0]
            dns_host_name = [string]$rootEntry.Attributes["dnsHostName"][0]
            search_base_ok = $searchBaseOk
        }
    }
    catch {
        return [PSCustomObject]@{
            ok = $false
            error = $_.Exception.Message
        }
    }
    finally {
        if ($connection) {
            $connection.Dispose()
        }
    }
}

function Test-PasswordResetPermission {
    param(
        [string]$Identity,
        [string]$Password
    )

    Import-Module ActiveDirectory -ErrorAction Stop

    try {
        $escapedIdentity = $Identity.Replace("(", "\28").Replace(")", "\29")
        $user = Get-ADUser -LDAPFilter "(|(sAMAccountName=$escapedIdentity)(userPrincipalName=$escapedIdentity)(mail=$escapedIdentity))" -Properties DisplayName, UserPrincipalName, mail

        if (-not $user) {
            return [PSCustomObject]@{
                ok = $false
                error = "Test user not found: $Identity"
            }
        }

        if (-not $Password) {
            return [PSCustomObject]@{
                ok = $false
                error = "TemporaryPassword is required when AttemptPasswordReset is used"
            }
        }

        $secure = ConvertTo-SecureString -String $Password -AsPlainText -Force
        Set-ADAccountPassword -Identity $user.DistinguishedName -Reset -NewPassword $secure
        Set-ADUser -Identity $user.DistinguishedName -ChangePasswordAtLogon $true

        return [PSCustomObject]@{
            ok = $true
            sam = [string]$user.SamAccountName
            upn = [string]$user.UserPrincipalName
            display_name = [string]$user.DisplayName
            reset_test = "success"
        }
    }
    catch {
        return [PSCustomObject]@{
            ok = $false
            error = $_.Exception.Message
        }
    }
}

try {
    Write-Log ("Starting LDAPS diagnostic for {0}:{1}" -f $Server, $Port)

    Write-Log "STEP: reading machine certificates"
    $certificates = @(Get-ServerAuthCertificates -DnsName $Server)
    Write-Log ("STEP OK: certificates found={0}" -f $certificates.Count)

    Write-Log "STEP: testing TCP connectivity"
    $portCheck = Test-LdapsSocket -HostName $Server -TcpPort $Port
    Write-Log ("STEP OK: tcp={0}" -f $portCheck.ok)

    Write-Log "STEP: testing LDAPS bind"
    $bindCheck = Test-LdapsBind -HostName $Server -TcpPort $Port -Username $BindUser -Password $BindPassword -BaseDn $SearchBase
    Write-Log ("STEP OK: ldaps_bind={0}" -f $bindCheck.ok)

    if ($AttemptPasswordReset) {
        Write-Log "STEP: testing password reset permission"
        $passwordResetCheck = Test-PasswordResetPermission -Identity $TestUserIdentity -Password $TemporaryPassword
        Write-Log ("STEP OK: password_reset={0}" -f $passwordResetCheck.ok)
    } else {
        $passwordResetCheck = [PSCustomObject]@{
            ok = $false
            skipped = $true
            note = "Skipped. Use -AttemptPasswordReset -TestUserIdentity user@domain -TemporaryPassword TempPass123! to perform a live reset test."
        }
        Write-Log "STEP SKIP: password reset test not requested"
    }

    $checks = [PSCustomObject]@{
        certificates = [PSCustomObject]@{
            ok = [bool]($certificates.Count -gt 0)
            certificates = @($certificates)
        }
        port_636 = $portCheck
        ldaps_bind = $bindCheck
        password_reset_test = $passwordResetCheck
    }

    $overallOk =
        $checks.certificates.ok -and
        $checks.port_636.ok -and
        $checks.ldaps_bind.ok -and
        (($checks.password_reset_test.PSObject.Properties.Name -contains "skipped" -and $checks.password_reset_test.skipped -eq $true) -or $checks.password_reset_test.ok)

    $report = [PSCustomObject]@{
        ok = [bool]$overallOk
        summary = $(if ($overallOk) { "LDAPS prerequisites look good" } else { "One or more LDAPS prerequisites failed or need attention" })
        generated_at = (Get-Date).ToString("o")
        server = $Server
        port = $Port
        checks = $checks
    }

    Write-Log $report.summary
    $report | ConvertTo-Json -Depth 8
    exit 0
}
catch {
    $message = $_.Exception.Message
    Write-Log ("FAILED: {0}" -f $message)

    [PSCustomObject]@{
        ok = $false
        summary = "LDAPS diagnostic failed"
        error = $message
        generated_at = (Get-Date).ToString("o")
        server = $Server
        port = $Port
    } | ConvertTo-Json -Depth 8

    exit 1
}
