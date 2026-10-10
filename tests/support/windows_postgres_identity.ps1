# Read-only OS evidence. No credential, service or configuration changes.
$ErrorActionPreference = 'Stop'
$stage = 'service-query'
try {
$service = Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-17'"
if (-not $service -or $service.State -ne 'Running') { $stage = 'service-not-running'; throw 'Evidence unavailable' }
$stage = 'listener-query'
$ports = @(Get-NetTCPConnection -State Listen -LocalPort 5432)
$stage = 'listener-ambiguous'
$ids = @($ports | Select-Object -ExpandProperty OwningProcess -Unique)
if ($ids.Count -ne 1) { throw 'Listener identity is ambiguous.' }
$stage = 'listener-process'
$listener = Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])"
$stage = 'service-process'
$serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($service.ProcessId)"
$stage = 'firewall-profiles'
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object {
  [ordered]@{ Name = $_.Name; Enabled = ($_.Enabled -eq 'True'); DefaultInboundAction = [string]$_.DefaultInboundAction }
})
$stage = 'firewall-rules'
$allows = @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow | ForEach-Object {
$stage = 'firewall-port-filter'
  $port = $_ | Get-NetFirewallPortFilter
$stage = 'firewall-application-filter'
  $app = $_ | Get-NetFirewallApplicationFilter
  # Match Python's relevance rules exactly. Preserve every rule, protocol,
  # program and port; omit only address queries Python cannot consult.
  $protocol = [string]$port.Protocol
  $program = [string]$app.Program
  $normalizedProgram = $program.ToLowerInvariant()
  $remote = @()
  if (($protocol -ne 'UDP' -and $protocol -ne '17') -and
      ($normalizedProgram -eq 'any' -or $normalizedProgram -eq 'system' -or
       $normalizedProgram.EndsWith('\postgres.exe'))) {
    $stage = 'firewall-address-filter'
    $address = $_ | Get-NetFirewallAddressFilter
    $remote = @($address.RemoteAddress)
  }
  [ordered]@{ protocol = $protocol; ports = @($port.LocalPort); program = $program; remote = $remote }
})
$stage = 'evidence-serialization'
[ordered]@{
  firewall_profiles = $profiles
  inbound_allow_rules = $allows
  service_name = $service.Name
  service_state = $service.State
  service_pid = [int]$service.ProcessId
  service_path = $service.PathName
  service_executable = $serviceProcess.ExecutablePath
  listener_pid = [int]$listener.ProcessId
  listener_parent_pid = [int]$listener.ParentProcessId
  listener_name = $listener.Name
  listener_executable = $listener.ExecutablePath
  listener_addresses = @($ports | Select-Object -ExpandProperty LocalAddress -Unique)
} | ConvertTo-Json -Depth 4 -Compress

} catch {
  # Emit only a fixed stage code; never raw exception text or command output.
  [ordered]@{ inspection_error = $stage } | ConvertTo-Json -Compress
  exit 1
}
