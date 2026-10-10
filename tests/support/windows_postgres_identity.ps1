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
$stage = 'listener-package-query'
# Read only process/token queries. No privilege adjustment or process mutation.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
public static class StockBridgePackageIdentity {
    [DllImport("kernel32.dll", SetLastError=true)]
    static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll")]
    static extern int GetPackageFullName(IntPtr process, ref uint length, IntPtr name);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool OpenProcessToken(IntPtr process, uint access, out IntPtr token);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool GetTokenInformation(IntPtr token, int kind, out int value, uint size, out uint returned);
    [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
    [DllImport("kernel32.dll")]
    static extern bool CloseHandle(IntPtr handle);
    public static string Inspect(uint pid) {
        IntPtr process = OpenProcess(0x1000, false, pid);
        if (process == IntPtr.Zero) throw new InvalidOperationException();
        IntPtr token = IntPtr.Zero;
        try {
            var image = new StringBuilder(32768); uint imageLength = 32768;
            if (!QueryFullProcessImageName(process, 0, image, ref imageLength)
                || !String.Equals(image.ToString(), @"C:\Program Files\PostgreSQL\17\bin\postgres.exe", StringComparison.OrdinalIgnoreCase))
                throw new InvalidOperationException();
            uint length = 0;
            int result = GetPackageFullName(process, ref length, IntPtr.Zero);
            if (result != 15700) throw new InvalidOperationException(); // APPMODEL_ERROR_NO_PACKAGE
            if (!OpenProcessToken(process, 0x0008, out token)) throw new InvalidOperationException();
            int tokenType; uint returned;
            if (!GetTokenInformation(token, 8, out tokenType, 4, out returned)
                || returned != 4 || tokenType != 1) throw new InvalidOperationException();
            int appContainer;
            if (!GetTokenInformation(token, 29, out appContainer, 4, out returned)
                || returned != 4 || appContainer != 0) throw new InvalidOperationException();
            return "unpackaged-non-appcontainer";
        } finally {
            if (token != IntPtr.Zero) CloseHandle(token);
            CloseHandle(process);
        }
    }
}
'@
$packageStatus = [StockBridgePackageIdentity]::Inspect([uint32]$listener.ProcessId)
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
  if (@($app).Count -ne 1 -or $null -eq $app.PSObject.Properties['Package']) { throw 'Package evidence unavailable' }
  $package = [string]$app.Package
  $normalizedProgram = $program.ToLowerInvariant()
  $remote = @()
  if (($protocol -ne 'UDP' -and $protocol -ne '17') -and
      ($normalizedProgram -eq 'any' -or $normalizedProgram -eq 'system' -or
       $normalizedProgram.EndsWith('\postgres.exe'))) {
    $stage = 'firewall-address-filter'
    $address = $_ | Get-NetFirewallAddressFilter
    $remote = @($address.RemoteAddress)
  }
  [ordered]@{ protocol = $protocol; ports = @($port.LocalPort); program = $program; package = $package; remote = $remote }
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
  listener_package_identity = [ordered]@{ method = 'win32-process-and-token'; status = $packageStatus; pid = [int]$listener.ProcessId }
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
