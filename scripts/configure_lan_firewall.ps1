#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding(SupportsShouldProcess = $true, DefaultParameterSetName = 'Apply')]
param(
    [string]$Root = 'D:\FacebookScraperService',
    [Parameter(Mandatory = $true, ParameterSetName = 'Apply')]
    [string]$LocalAddress,
    [Parameter(Mandatory = $true, ParameterSetName = 'Apply')]
    [ValidateScript({ $_ -and -not [System.Management.Automation.WildcardPattern]::ContainsWildcardCharacters($_) })]
    [string]$InterfaceAlias,
    [Parameter(ParameterSetName = 'Apply')]
    [ValidateSet('Domain', 'Private')]
    [string[]]$Profile = @('Domain', 'Private'),
    [Parameter(Mandatory = $true, ParameterSetName = 'Remove')]
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$ruleName = 'FBScraper-LAN-Web'
$ruleGroup = 'FBScraper Managed Access'
$displayName = 'FBScraper office review HTTP'

# Read errors must not be mistaken for an absent rule. Only this exact owned name is changed.
$existing = @(Get-NetFirewallRule -PolicyStore PersistentStore -ErrorAction Stop |
    Where-Object { $_.Name -eq $ruleName })
if ($existing.Count -gt 1 -or ($existing.Count -eq 1 -and $existing[0].Group -cne $ruleGroup)) {
    throw 'Firewall rule ownership mismatch; inspect the existing rule manually.'
}

if ($Remove) {
    $applied = $false
    if ($existing.Count -eq 1 -and $PSCmdlet.ShouldProcess($ruleName, 'Remove owned LAN firewall rule')) {
        Remove-NetFirewallRule -Name $ruleName -PolicyStore PersistentStore -Confirm:$false -ErrorAction Stop
        $applied = $true
    }
    [pscustomobject]@{ Action = 'Remove'; Name = $ruleName; Applied = $applied }
    return
}

$resolvedRoot = (Resolve-Path -LiteralPath $Root -ErrorAction Stop).Path
$controller = Join-Path $resolvedRoot 'controller'
$python = Join-Path $controller '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Installed controller Python was not found.'
}
$readPolicy = @'
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from core.web_access import load_web_access
policy = load_web_access(Path(sys.argv[2]) / 'control')
if policy.web_host != '0.0.0.0':
    raise ValueError('lan_binding_required')
print(json.dumps(policy.as_dict()))
'@
$policyJson = & $python -I -c $readPolicy $controller $resolvedRoot
if ($LASTEXITCODE -ne 0) {
    throw 'Installed LAN access policy is invalid.'
}
$policy = $policyJson | ConvertFrom-Json -ErrorAction Stop

$parsedAddress = $null
if (-not [System.Net.IPAddress]::TryParse($LocalAddress, [ref]$parsedAddress) -or
    $parsedAddress.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or
    $parsedAddress.ToString() -cne $LocalAddress) {
    throw 'LocalAddress must be a literal IPv4 address.'
}
$addresses = @(Get-NetIPAddress -InterfaceAlias $InterfaceAlias -AddressFamily IPv4 -ErrorAction Stop |
    Where-Object { $_.IPAddress -ceq $LocalAddress -and $_.InterfaceAlias -ceq $InterfaceAlias -and
        $_.AddressState -eq 'Preferred' })
if ($addresses.Count -ne 1) {
    throw 'LocalAddress is not an active IPv4 address on the selected interface.'
}
$connections = @(Get-NetConnectionProfile -InterfaceIndex $addresses[0].InterfaceIndex -ErrorAction Stop)
if ($connections.Count -eq 0 -or @($connections | Where-Object {
    $_.NetworkCategory -notin @('DomainAuthenticated', 'Private')
}).Count -gt 0) {
    throw 'The selected interface must have a Domain or Private network profile.'
}

$parameters = @{
    Name = $ruleName
    PolicyStore = 'PersistentStore'
    Enabled = 'True'
    Direction = 'Inbound'
    Action = 'Allow'
    Profile = $Profile
    Protocol = 'TCP'
    LocalPort = [string]$policy.web_port
    RemotePort = 'Any'
    LocalAddress = $LocalAddress
    RemoteAddress = @($policy.allowed_client_cidrs)
    InterfaceAlias = $InterfaceAlias
    # A release-specific executable path becomes stale after automatic updates.
    Program = 'Any'
    EdgeTraversalPolicy = 'Block'
}
$operation = if ($existing.Count -eq 1) { 'Update' } else { 'Create' }
$scopeDescription = "${operation}: TCP $($policy.web_port), $LocalAddress, $InterfaceAlias, sources " +
    ($policy.allowed_client_cidrs -join ',') + ', profiles ' + ($Profile -join ',')
$applied = $false
if ($PSCmdlet.ShouldProcess($ruleName, $scopeDescription)) {
    if ($existing.Count -eq 1) {
        Set-NetFirewallRule @parameters -NewDisplayName $displayName -Confirm:$false -ErrorAction Stop
    } else {
        New-NetFirewallRule @parameters -DisplayName $displayName -Group $ruleGroup -Confirm:$false -ErrorAction Stop |
            Out-Null
    }
    $applied = $true
}
[pscustomobject]@{
    Action = $operation; Name = $ruleName; LocalAddress = $LocalAddress; InterfaceAlias = $InterfaceAlias
    Profile = $Profile; LocalPort = $policy.web_port; RemoteAddress = @($policy.allowed_client_cidrs)
    Applied = $applied
}
