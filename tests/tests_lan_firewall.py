"""Manual firewall helper against mock PowerShell cmdlets and a temporary install."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / 'scripts/configure_lan_firewall.ps1'
RULE = {'Name': 'FBScraper-LAN-Web', 'Group': 'FBScraper Managed Access'}
POLICY = {'web_host': '0.0.0.0', 'web_port': 9876,
          'public_base_url': 'http://192.168.20.10:9876',
          'allowed_client_cidrs': ['192.168.20.0/24', '10.40.0.0/16']}

HARNESS = r'''
param([string]$Helper, [string]$InstallRoot, [string]$CaseFile)
$ErrorActionPreference = 'Stop'
$case = Get-Content -LiteralPath $CaseFile -Raw | ConvertFrom-Json
$global:Recorded = @()
$global:Rules = @($case.rules)
function Get-NetIPAddress {
    param($InterfaceAlias, $AddressFamily, $ErrorAction)
    [pscustomobject]@{IPAddress=$case.ip; InterfaceAlias='Office Ethernet'; InterfaceIndex=7; AddressState='Preferred'}
}
function Get-NetConnectionProfile {
    param($InterfaceIndex, $ErrorAction)
    [pscustomobject]@{NetworkCategory=$case.category}
}
function Get-NetFirewallRule {
    param($PolicyStore, $ErrorAction)
    if ($case.query_error) { throw 'fixture_query_failed' }
    $global:Rules
}
function Save-FixtureCall($Kind, $Bound) {
    $record = @{kind=$Kind}
    foreach ($key in $Bound.Keys) { $record[$key] = $Bound[$key] }
    $global:Recorded += $record
}
function New-NetFirewallRule {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param($Name, $PolicyStore, $DisplayName, $Group, $Enabled, $Direction, $Action,
          $Profile, $Protocol, $LocalPort, $RemotePort, $LocalAddress, $RemoteAddress,
          $InterfaceAlias, $Program, $EdgeTraversalPolicy)
    Save-FixtureCall 'new' $PSBoundParameters
    $global:Rules += [pscustomobject]@{Name=$Name;Group=$Group}
}
function Set-NetFirewallRule {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param($Name, $PolicyStore, $NewDisplayName, $Enabled, $Direction, $Action,
          $Profile, $Protocol, $LocalPort, $RemotePort, $LocalAddress, $RemoteAddress,
          $InterfaceAlias, $Program, $EdgeTraversalPolicy)
    Save-FixtureCall 'set' $PSBoundParameters
}
function Remove-NetFirewallRule {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param($Name, $PolicyStore)
    Save-FixtureCall 'remove' $PSBoundParameters
}
# No Net* cmdlet may resolve to an operating-system module in this fixture.
$PSModuleAutoLoadingPreference = 'None'
$source = Get-Content -LiteralPath $Helper -Raw
$tokens = $null; $errors = $null
[void][System.Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) { throw 'helper_parse_failed' }
$source = $source -replace '(?im)^#requires[^\r\n]*(?:\r?\n|$)', ''
$script = [scriptblock]::Create($source)
$arguments = @{Root=$InstallRoot}
if ($case.remove) { $arguments.Remove=$true }
else {
    $arguments.LocalAddress='192.168.20.10'
    $arguments.InterfaceAlias=$case.alias
    $arguments.Profile=@($case.profile)
}
if ($case.what_if) { $arguments.WhatIf=$true }
$ok = $false; $errorText = ''; $plans = @()
try {
    $plans += & $script @arguments
    if ($case.repeat) { $plans += & $script @arguments }
    $ok = $true
} catch { $errorText = $_.Exception.Message }
@{ok=$ok; error=$errorText; calls=@($global:Recorded); plans=$plans} | ConvertTo-Json -Depth 8 -Compress
'''


@unittest.skipUnless(os.name == 'nt', 'Windows PowerShell fixture')
class FirewallTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name) / 'installed service'
        (cls.root / 'control').mkdir(parents=True)
        controller = cls.root / 'controller'
        (controller / 'core').mkdir(parents=True)
        (controller / 'core/__init__.py').write_bytes(b'')
        shutil.copyfile(ROOT / 'core/web_access.py', controller / 'core/web_access.py')
        result = subprocess.run([sys.executable, '-m', 'venv', '--without-pip', str(controller / '.venv')],
                                capture_output=True, text=True)
        if result.returncode:
            raise RuntimeError(result.stderr)
        cls.harness = Path(cls.temp.name) / 'mock-firewall.ps1'
        cls.harness.write_text(HARNESS, encoding='ascii', newline='')

    def setUp(self):
        (self.root / 'control/host.json').write_text(json.dumps(POLICY), encoding='ascii', newline='')

    def run_helper(self, **overrides):
        case = dict(rules=[], ip='192.168.20.10', category='Private', alias='Office Ethernet',
                    profile=['Domain', 'Private'], query_error=False, remove=False, what_if=False, repeat=False)
        case.update(overrides)
        casefile = Path(self.temp.name) / 'case.json'
        casefile.write_text(json.dumps(case), encoding='ascii', newline='')
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                                 '-File', str(self.harness), '-Helper', str(HELPER),
                                 '-InstallRoot', str(self.root), '-CaseFile', str(casefile)],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_create_then_update_scopes_only_owned_rule_and_clears_old_program(self):
        result = self.run_helper(repeat=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual([row['kind'] for row in result['calls']], ['new', 'set'])
        for row in result['calls']:
            for key, expected in dict(Name=RULE['Name'], PolicyStore='PersistentStore', Direction='Inbound',
                                      Action='Allow', Protocol='TCP', LocalPort='9876', RemotePort='Any',
                                      LocalAddress='192.168.20.10', InterfaceAlias='Office Ethernet',
                                      RemoteAddress=POLICY['allowed_client_cidrs'], Profile=['Domain', 'Private'],
                                      Program='Any', Enabled='True', EdgeTraversalPolicy='Block').items():
                self.assertEqual(row[key], expected, key)
        self.assertEqual(result['calls'][0]['Group'], RULE['Group'])

    def test_what_if_validates_and_displays_without_mutating(self):
        for remove, rules in ((False, []), (True, [RULE])):
            with self.subTest(remove=remove):
                result = self.run_helper(what_if=True, remove=remove, rules=rules)
                self.assertTrue(result['ok'], result)
                self.assertEqual(result['calls'], [])
                self.assertFalse(result['plans'][-1]['Applied'])

    def test_revoke_only_owned_rule_and_absent_rule_is_idempotent(self):
        other = {'Name': 'SomeOtherPythonRule', 'Group': 'Another owner'}
        result = self.run_helper(remove=True, rules=[RULE, other])
        self.assertTrue(result['ok'], result)
        self.assertEqual(len(result['calls']), 1)
        self.assertEqual(result['calls'][0]['kind'], 'remove')
        self.assertEqual(result['calls'][0]['Name'], RULE['Name'])
        self.assertEqual(self.run_helper(remove=True, rules=[other])['calls'], [])

    def test_foreign_named_rule_is_never_updated_or_removed(self):
        for remove in (False, True):
            with self.subTest(remove=remove):
                result = self.run_helper(remove=remove, rules=[dict(RULE, Group='Another owner')])
                self.assertFalse(result['ok'], result)
                self.assertIn('ownership', result['error'])
                self.assertEqual(result['calls'], [])

    def test_invalid_interface_profile_or_query_never_creates_rule(self):
        for change in ({'ip': '192.168.20.99'}, {'category': 'Public'}, {'profile': ['Public']},
                       {'alias': '*'}, {'query_error': True}):
            with self.subTest(change=change):
                result = self.run_helper(**change)
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['calls'], [])

    def test_missing_broken_or_local_policy_never_creates_rule(self):
        for raw in (None, '{broken', json.dumps({'web_port': 9876}), json.dumps(dict(POLICY, allowed_client_cidrs=[]))):
            with self.subTest(raw=raw):
                path = self.root / 'control/host.json'
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_text(raw, encoding='ascii', newline='')
                result = self.run_helper()
                self.assertFalse(result['ok'], result)
                self.assertEqual(result['calls'], [])

    def test_script_is_ascii_and_requires_technical_administrator(self):
        source = HELPER.read_bytes().decode('ascii')
        self.assertIn('#Requires -RunAsAdministrator', source)
        self.assertIn('#Requires -Version 5.1', source)


if __name__ == '__main__':
    unittest.main()
