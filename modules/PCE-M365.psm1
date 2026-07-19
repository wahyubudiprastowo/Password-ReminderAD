function Connect-PCEGraph {
    param($Config)
    Import-Module Microsoft.Graph.Authentication -ErrorAction Stop
    Import-Module Microsoft.Graph.Users.Actions -ErrorAction Stop
    if ($Config.M365.ClientSecret) {
        $secureClientSecret = ConvertTo-SecureString -String $Config.M365.ClientSecret -AsPlainText -Force
        $clientSecretCredential = New-Object System.Management.Automation.PSCredential (
            $Config.M365.ClientId,
            $secureClientSecret
        )
        Connect-MgGraph -TenantId $Config.M365.TenantId -ClientSecretCredential $clientSecretCredential -NoWelcome
    } else {
        Connect-MgGraph -TenantId $Config.M365.TenantId -ClientId $Config.M365.ClientId -CertificateThumbprint $Config.M365.CertificateThumbprint -NoWelcome
    }
    Write-Host "[M365] Connected" -ForegroundColor Green
}
function Revoke-PCEM365Sessions {
    [CmdletBinding(SupportsShouldProcess)]
    param([string]$Upn)
    if ($PSCmdlet.ShouldProcess($Upn,"Revoke")) {
        try {
            Revoke-MgUserSignInSession -UserId $Upn -ErrorAction Stop | Out-Null
            Write-Host "[M365] Revoked $Upn" -ForegroundColor Yellow
        } catch { Write-Warning "[M365] $Upn : $_" }
    }
}
Export-ModuleMember -Function *
