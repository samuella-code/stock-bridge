# Read-only OS evidence. No credential, service or configuration changes.
$ErrorActionPreference = 'Stop'
$service = Get-CimInstance Win32_Service -Filter "Name='postgresql-x64-17'"
if (-not $service -or $service.State -ne 'Running') { throw 'Expected PostgreSQL service is not running.' }
$ports = @(Get-NetTCPConnection -State Listen -LocalPort 5432)
$ids = @($ports | Select-Object -ExpandProperty OwningProcess -Unique)
if ($ids.Count -ne 1) { throw 'Listener identity is ambiguous.' }
$listener = Get-CimInstance Win32_Process -Filter "ProcessId=$($ids[0])"
$serviceProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($service.ProcessId)"
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object {
  [ordered]@{ Name = $_.Name; Enabled = ($_.Enabled -eq 'True'); DefaultInboundAction = [string]$_.DefaultInboundAction }
})
$allows = @(Get-NetFirewallRule -PolicyStore ActiveStore -Enabled True -Direction Inbound -Action Allow | ForEach-Object {
  $port = $_ | Get-NetFirewallPortFilter
  $app = $_ | Get-NetFirewallApplicationFilter
  $address = $_ | Get-NetFirewallAddressFilter
  [ordered]@{ protocol = [string]$port.Protocol; ports = @($port.LocalPort); program = [string]$app.Program; remote = @($address.RemoteAddress) }
})
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
