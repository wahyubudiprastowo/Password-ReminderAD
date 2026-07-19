param(
    [Parameter(Mandatory = $true)]
    [string]$ServerFqdn,

    [string]$OutputDir = "C:\ProgramData\PCE\ldaps",

    [string]$IssuedCertPath,

    [switch]$CreateRequestOnly,

    [switch]$AcceptIssuedCertificate,

    [switch]$RestartAfterAccept
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
            subject = $cert.Subject
            thumbprint = $cert.Thumbprint
            not_after = $cert.NotAfter
            has_private_key = [bool]$cert.HasPrivateKey
            has_server_auth = [bool]$hasServerAuth
            matches_server_name = [bool]$matchesName
            dns_hint = $sanText
        })
    }

    return @($results.ToArray())
}

if (-not (Test-Admin)) {
    throw "Run this script from an elevated PowerShell session."
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$requestInfPath = Join-Path $OutputDir "request.inf"
$requestReqPath = Join-Path $OutputDir "request.req"
$acceptLogPath = Join-Path $OutputDir "accept.log"

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

$requestInf = $requestInf.Replace("{SERVER_FQDN}", $ServerFqdn)

Set-Content -LiteralPath $requestInfPath -Value $requestInf -Encoding ASCII
Write-Ok ("Request INF created: {0}" -f $requestInfPath)

Write-Info "Scanning LocalMachine\\My for LDAPS candidate certificates"
$candidateCerts = @(Get-LdapsCandidateCertificates -DnsName $ServerFqdn)
$validCandidates = @($candidateCerts | Where-Object {
    $_.has_private_key -and $_.has_server_auth -and $_.matches_server_name
})

if ($validCandidates.Count -gt 0) {
    Write-WarnLine ("Found {0} existing candidate certificate(s) already matching LDAPS requirements." -f $validCandidates.Count)
    $validCandidates | Format-Table subject, thumbprint, not_after, has_private_key, has_server_auth, matches_server_name -AutoSize
} else {
    Write-Info "No existing LDAPS-ready certificate found in LocalMachine\\My."
}

Write-Info "Generating CSR with certreq"
& certreq.exe -new $requestInfPath $requestReqPath
if ($LASTEXITCODE -ne 0) {
    throw "certreq -new failed."
}
Write-Ok ("CSR created: {0}" -f $requestReqPath)

if ($CreateRequestOnly) {
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor White
    Write-Host ("1. Submit CSR file to your internal CA: {0}" -f $requestReqPath)
    Write-Host "2. Ensure issued certificate contains Server Authentication and CN/SAN = DC FQDN."
    Write-Host "3. Re-run this script with -AcceptIssuedCertificate -IssuedCertPath <path-to-cer>."
    exit 0
}

if ($AcceptIssuedCertificate) {
    if (-not $IssuedCertPath) {
        throw "-IssuedCertPath is required when -AcceptIssuedCertificate is used."
    }
    if (-not (Test-Path -LiteralPath $IssuedCertPath)) {
        throw ("Issued certificate file not found: {0}" -f $IssuedCertPath)
    }

    Write-Info ("Accepting issued certificate: {0}" -f $IssuedCertPath)
    & certreq.exe -accept $IssuedCertPath *> $acceptLogPath
    if ($LASTEXITCODE -ne 0) {
        throw ("certreq -accept failed. See log: {0}" -f $acceptLogPath)
    }
    Write-Ok "Issued certificate accepted into the machine certificate store."

    Write-Host ""
    Write-Host "Recommended verification after accept:" -ForegroundColor White
    Write-Host ("- Run: ldp.exe -> Connect -> {0}:636 with SSL checked" -f $ServerFqdn)
    Write-Host "- If there are multiple valid certs, consider cleaning old conflicting LDAPS certificates first."
    Write-Host "- If LDAPS still fails, reboot the DC so Schannel/AD DS picks the new certificate."

    if ($RestartAfterAccept) {
        Write-WarnLine "Restarting the domain controller in 15 seconds..."
        Start-Sleep -Seconds 15
        Restart-Computer -Force
    }

    exit 0
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor White
Write-Host ("1. Submit CSR file to your internal CA: {0}" -f $requestReqPath)
Write-Host "2. Save issued certificate as .cer or .crt."
Write-Host ("3. Re-run: .\PCE-LDAPS-Prepare.ps1 -ServerFqdn {0} -AcceptIssuedCertificate -IssuedCertPath <issued-cert-path>" -f $ServerFqdn)
