. (Join-Path $PSScriptRoot 'app-process-common.ps1')
if (-not (Test-Path -LiteralPath $statePath)) { Write-Host 'No managed app process.'; exit 0 }
$state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
$managed = @(Get-ManagedAppProcesses $state)
# Validate the entire tree before stopping anything. Kill exact Process handles,
# deepest descendants first, so a Windows venv launcher cannot leave uvicorn behind.
foreach ($item in @($managed | Sort-Object depth -Descending)) {
    $process = $item.process
    $process.Refresh()
    if (-not $process.HasExited) {
        $current = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
        if (-not $current) { continue }
        if (-not (Test-AppIdentity $process $item) -or $current.CommandLine -ne $item.info.CommandLine -or $current.ParentProcessId -ne $item.info.ParentProcessId) {
            throw 'App process changed during shutdown. Refusing to control it.'
        }
        $process.Kill()
        if (-not $process.WaitForExit(10000)) { throw 'App process did not exit; runtime state retained.' }
    }
}
Remove-Item -LiteralPath $statePath
Write-Host 'Project Manager stopped. PostgreSQL remains running; stop it with scripts/stop-database.ps1.'
