param(
    [Parameter(Mandatory = $true)]
    [string]$ServerFqdn,

    [string]$OutputDir = "C:\ProgramData\PCE\ldaps",

    [string]$IssuedCertPath,

    [string]$Thumbprint,

    [switch]$GenerateCsr,

    [switch]$UseBestExistingCandidate,

    [switch]$PromoteToNtdsStore,

    [switch]$RestartAfterRemediation
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Info {
    param([string]$Message)
    Write-Host ("[INFO] {0}" -f $Message) -ForegroundColor Cyan
}

function Write-Ok {
    param([string]$Message)
    Write-Host ("[OK]   {0}" -f $Message) -ForegroundColor Green
}

function Write-WarnLine {
    param([string]$Message)
    Write-Host ("[WARN] {0}" -f $Message) -ForegroundColor Yellow
}

function Write-Step {
    param([string]$Message)
    Write-Host ("[STEP] {0}" -f $Message) -ForegroundColor White
}

function Test-Admin {
    $current = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($current)
    return $principal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)
}

function Get-LdapsCandidateCertificates {
    param([string]$DnsName)

    $serverAuthOid = "1.3.6.1.5.5.7.3.1"
    $certs = @(Get-ChildItem Cert:\LocalMachine\My | Sort-Object NotAfter -Descending)
    $results = New-Object System.Collections.ArrayList

    foreach ($cert in $certs) {
        $hasServerAuth = $false
        foreach ($ext in $cert.Extensions) {
            if ($ext.Oid.Value -ne "2.5.29.37") {
                continue
            }

            try {
                $eku = New-Object System.Security.Cryptography.X509Certificates.X509EnhancedKeyUsageExtension($ext, $false)
                foreach ($oid in $eku.EnhancedKeyUsages) {
                    if ($oid.Value -eq $serverAuthOid) {
                        $hasServerAuth = $true
                        break
                    }
                }
            } catch {}

            if ($hasServerAuth) {
                break
            }
        }

        $sanText = ""
        try {
            $sanExt = $cert.Extensions | Where-Object { $_.Oid.FriendlyName -eq "Subject Alternative Name" } | Select-Object -First 1
            if ($sanExt) {
                $sanText = $sanExt.Format($false)
            }
        } catch {}

        $matchesName = $false
        if ($cert.Subject -match [regex]::Escape($DnsName)) {
            $matchesName = $true
        } elseif ($sanText -match [regex]::Escape($DnsName)) {
            $matchesName = $true
        }

        [void]$results.Add([PSCustomObject]@{
            subject = [string]$cert.Subject
            issuer = [string]$cert.Issuer
            thumbprint = [string]$cert.Thumbprint
            not_before = $cert.NotBefore
            not_after = $cert.NotAfter
            has_private_key = [bool]$cert.HasPrivateKey
            has_server_auth = [bool]$hasServerAuth
            matches_server_name = [bool]$matchesName
            self_signed = [bool]($cert.Subject -eq $cert.Issuer)
            dns_hint = $sanText
        })
    }

    return @($results.ToArray())
}

function New-CsrFile {
    param(
        [string]$DnsName,
        [string]$BaseDir
    )

    $requestInfPath = Join-Path $BaseDir "request.inf"
    $requestReqPath = Join-Path $BaseDir "request.req"

    $requestInf = @'
[Version]
Signature="$Windows NT$"

[NewRequest]
Subject = "CN={SERVER_FQDN}"
KeySpec = 1
KeyLength = 2048
Exportable = TRUE
MachineKeySet = TRUE
SMIME = FALSE
PrivateKeyArchive = FALSE
UserProtected = FALSE
UseExistingKeySet = FALSE
ProviderName = "Microsoft RSA SChannel Cryptographic Provider"
ProviderType = 12
RequestType = PKCS10
KeyUsage = 0xa0
FriendlyName = "LDAPS - {SERVER_FQDN}"

[Extensions]
2.5.29.17 = "{text}"
_continue_ = "dns={SERVER_FQDN}"

[EnhancedKeyUsageExtension]
OID=1.3.6.1.5.5.7.3.1
'@

    $requestInf = $requestInf.Replace("{SERVER_FQDN}", $DnsName)
    Set-Content -LiteralPath $requestInfPath -Value $requestInf -Encoding ASCII
    Write-Ok ("Request INF created: {0}" -f $requestInfPath)

    & certreq.exe -new $requestInfPath $requestReqPath
    if ($LASTEXITCODE -ne 0) {
        throw "certreq -new failed."
    }

    Write-Ok ("CSR created: {0}" -f $requestReqPath)
    return $requestReqPath
}

function Accept-IssuedCertificate {
    param([string]$Path)

    $acceptLogPath = Join-Path $OutputDir "accept.log"
    Write-Info ("Accepting issued certificate: {0}" -f $Path)
    & certreq.exe -accept $Path *> $acceptLogPath
    if ($LASTEXITCODE -ne 0) {
        throw ("certreq -accept failed. See log: {0}" -f $acceptLogPath)
    }
    Write-Ok "Issued certificate accepted into LocalMachine\\My."
}

function Resolve-TargetCertificate {
    param(
        [string]$DnsName,
        [string]$WantedThumbprint,
        [switch]$PickBest
    )

    $candidates = @(Get-LdapsCandidateCertificates -DnsName $DnsName)
    $valid = @($candidates | Where-Object {
        $_.has_private_key -and $_.has_server_auth -and $_.matches_server_name
    } | Sort-Object not_after -Descending)

    if ($valid.Count -eq 0) {
        return $null
    }

    if ($WantedThumbprint) {
        $normalized = ($WantedThumbprint -replace '\s','').ToUpperInvariant()
        return $valid | Where-Object { $_.thumbprint.ToUpperInvariant() -eq $normalized } | Select-Object -First 1
    }

    if ($PickBest) {
        return $valid | Select-Object -First 1
    }

    return $valid | Select-Object -First 1
}

function Get-CertificateFromLocalMy {
    param([string]$CertThumbprint)

    $normalized = ($CertThumbprint -replace '\s','').ToUpperInvariant()
    $storePath = "Cert:\LocalMachine\My\{0}" -f $normalized
    if (-not (Test-Path -LiteralPath $storePath)) {
        return $null
    }

    return Get-Item -LiteralPath $storePath
}

function Test-KeyAssociation {
    param([string]$CertThumbprint)

    $cert = Get-CertificateFromLocalMy -CertThumbprint $CertThumbprint
    if (-not $cert) {
        throw ("Certificate not found in LocalMachine\My: {0}" -f $CertThumbprint)
    }

    return [PSCustomObject]@{
        thumbprint = $cert.Thumbprint
        subject = $cert.Subject
        has_private_key = [bool]$cert.HasPrivateKey
    }
}

function Export-AndImportToNtds {
    param([string]$CertThumbprint)

    $normalized = ($CertThumbprint -replace '\s','').ToUpperInvariant()
    $tempCerPath = Join-Path $OutputDir ("ldaps-{0}.cer" -f $normalized)
    $cert = Get-CertificateFromLocalMy -CertThumbprint $normalized

    if (-not $cert) {
        throw ("Certificate not found in LocalMachine\My: {0}" -f $normalized)
    }
    Write-Step ("Exporting certificate {0} to temporary CER" -f $normalized)

    $exported = $false
    try {
        $bytes = $cert.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
        [System.IO.File]::WriteAllBytes($tempCerPath, $bytes)
        $exported = $true
    } catch {
        try {
            Export-Certificate -Cert $cert -FilePath $tempCerPath -Force | Out-Null
            $exported = $true
        } catch {}
    }

    if (-not $exported -or -not (Test-Path -LiteralPath $tempCerPath)) {
        throw ("Failed to export certificate {0} from LocalMachine\\My." -f $normalized)
    }
    Write-Ok ("Exported CER: {0}" -f $tempCerPath)

    Write-Step "Importing certificate into NTDS personal store"
    & certutil.exe -f -addstore NTDS $tempCerPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        & certutil.exe -f -service NTDS -addstore My $tempCerPath | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to import certificate into NTDS store."
        }
    }
    Write-Ok "Certificate imported into NTDS store."

    Write-Step "Verifying NTDS store contents"
    & certutil.exe -store NTDS $normalized
}

function Show-LdapsEvents {
    try {
        Write-Step "Recent LDAP Interface events"
        Get-WinEvent -LogName "Directory Service" -MaxEvents 20 |
            Where-Object { $_.Id -in 1220, 2886, 2887, 2888, 2889 } |
            Select-Object TimeCreated, Id, LevelDisplayName, Message |
            Format-List
    } catch {
        Write-WarnLine ("Unable to query Directory Service events: {0}" -f $_.Exception.Message)
    }
}

if (-not (Test-Admin)) {
    throw "Run this script from an elevated PowerShell session."
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

Write-Step "Scanning LocalMachine\\My for LDAPS candidate certificates"
$allCandidates = @(Get-LdapsCandidateCertificates -DnsName $ServerFqdn)
$validCandidates = @($allCandidates | Where-Object {
    $_.has_private_key -and $_.has_server_auth -and $_.matches_server_name
} | Sort-Object not_after -Descending)

if ($validCandidates.Count -gt 0) {
    Write-WarnLine ("Found {0} LDAPS candidate certificate(s)." -f $validCandidates.Count)
    $validCandidates | Format-Table subject, issuer, thumbprint, not_after, self_signed -AutoSize
} else {
    Write-WarnLine "No LDAPS candidate certificate found in LocalMachine\\My."
}

if ($GenerateCsr -or ((-not $IssuedCertPath) -and (-not $UseBestExistingCandidate) -and (-not $Thumbprint))) {
    Write-Step "Generating CSR for CA issuance"
    $csrPath = New-CsrFile -DnsName $ServerFqdn -BaseDir $OutputDir
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor White
    Write-Host ("1. Submit CSR to your CA: {0}" -f $csrPath)
    Write-Host ("2. When cert is issued, run: .\PCE-LDAPS-Repair.ps1 -ServerFqdn {0} -IssuedCertPath <path-to-cer> -PromoteToNtdsStore" -f $ServerFqdn)
    Show-LdapsEvents
    exit 0
}

if ($IssuedCertPath) {
    if (-not (Test-Path -LiteralPath $IssuedCertPath)) {
        throw ("Issued certificate file not found: {0}" -f $IssuedCertPath)
    }
    Write-Step "Accepting CA-issued certificate"
    Accept-IssuedCertificate -Path $IssuedCertPath
}

$target = Resolve-TargetCertificate -DnsName $ServerFqdn -WantedThumbprint $Thumbprint -PickBest:$UseBestExistingCandidate
if (-not $target) {
    throw "No suitable LDAPS certificate could be selected. Generate/import a CA-issued certificate first."
}

Write-Step "Selected certificate for remediation"
$target | Format-List

if ($target.self_signed) {
    Write-WarnLine "Selected certificate is self-signed. This may still fail for LDAPS clients if the trust chain is not accepted."
}

Write-Step "Verifying key association"
$keyCheck = Test-KeyAssociation -CertThumbprint $target.thumbprint
$keyCheck | Format-List

if ($PromoteToNtdsStore) {
    Export-AndImportToNtds -CertThumbprint $target.thumbprint
    Write-Ok "Preferred LDAPS certificate promoted to NTDS store."
}

Show-LdapsEvents

Write-Host ""
Write-Host "Recommended verification:" -ForegroundColor White
Write-Host ("- Run local test: ldp.exe -> Connect -> {0}:636 with SSL checked" -f $ServerFqdn)
Write-Host ("- Run app-side test after trust is correct: openssl s_client -connect <dc-ip>:636 -showcerts </dev/null")

if ($RestartAfterRemediation) {
    Write-WarnLine "Restarting the domain controller in 15 seconds..."
    Start-Sleep -Seconds 15
    Restart-Computer -Force
    exit 0
}

Write-Host ""
Write-Host "If LDAPS still fails after promotion, the usual next step is a planned DC reboot." -ForegroundColor Yellow
