[CmdletBinding(SupportsShouldProcess = $true)]
param()

$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated PowerShell session.'
}

# Separate names preserve the existing legacy HTTP rule. No rules are removed.
$rules = @(
    @{ Name = 'EnglishPractice-LAN-TLS-v2'; Protocol = 'TCP'; Port = 8767 },
    @{ Name = 'EnglishPractice-LAN-mDNS-v2'; Protocol = 'UDP'; Port = 5353 }
)
foreach ($rule in $rules) {
    $existing = Get-NetFirewallRule -Name $rule.Name -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Output ($rule.Name + ' already exists; verify it before changing its scope.')
        continue
    }
    if ($PSCmdlet.ShouldProcess($rule.Name, 'Allow inbound traffic from LocalSubnet on Private networks')) {
        $parameters = @{ Name = $rule.Name; DisplayName = $rule.Name; Direction = 'Inbound'; Action = 'Allow'; Protocol = $rule.Protocol; LocalPort = $rule.Port; RemoteAddress = 'LocalSubnet'; Profile = 'Private' }
        New-NetFirewallRule @parameters | Out-Null
    }
}
