$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$LocalRoot = Join-Path $ProjectRoot '.local'
$statePath = Join-Path $LocalRoot 'app-runtime.json'
$AppPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$AppBackend = Join-Path $ProjectRoot 'backend'

function ConvertTo-AppUtc([object]$Timestamp) {
    # PowerShell 7.5 ConvertFrom-Json can deserialize ISO strings as DateTime.
    if ($Timestamp -is [DateTimeOffset]) { return $Timestamp.UtcDateTime }
    if ($Timestamp -is [DateTime]) { return $Timestamp.ToUniversalTime() }
    return [DateTimeOffset]::Parse([string]$Timestamp, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind).UtcDateTime
}

function Test-AppIdentity([Diagnostics.Process]$Process, [object]$Record) {
    return $Process.StartTime.ToUniversalTime().Ticks -eq (ConvertTo-AppUtc $Record.started_at).Ticks -and
        [IO.Path]::GetFullPath($Process.Path) -eq [IO.Path]::GetFullPath($Record.executable)
}

function Get-AppPythonExecutables {
    $allowed = @($AppPython)
    $venvConfig = Join-Path $ProjectRoot '.venv\pyvenv.cfg'
    if (Test-Path -LiteralPath $venvConfig) {
        $homeSetting = Get-Content -LiteralPath $venvConfig | Where-Object { $_ -match '^home\s*=\s*(.+)$' } | Select-Object -First 1
        if ($homeSetting -match '^home\s*=\s*(.+)$') { $allowed += [IO.Path]::GetFullPath((Join-Path $Matches[1].Trim() 'python.exe')) }
    }
    return $allowed
}

function Test-AppServerCommand([object]$Info) {
    if (-not $Info.ExecutablePath -or [IO.Path]::GetFullPath($Info.ExecutablePath) -notin (Get-AppPythonExecutables)) { return $false }
    $pythonPattern = [regex]::Escape($AppPython)
    $backendPattern = [regex]::Escape($AppBackend)
    return $Info.CommandLine -match ('^\s*"' + $pythonPattern + '"\s+-m\s+uvicorn\s+app\.main:app\s+--app-dir\s+"' + $backendPattern + '"\s+--host\s+127\.0\.0\.1\s+--port\s+8000\s*$')
}

function Get-ManagedAppProcesses([object]$State) {
    if ([IO.Path]::GetFullPath($State.root) -ne $ProjectRoot -or [IO.Path]::GetFullPath($State.executable) -ne $AppPython) {
        throw 'The recorded app process belongs to a different workspace or executable.'
    }
    $records = @($State)
    if (-not (Get-Process -Id $State.pid -ErrorAction SilentlyContinue) -and $State.PSObject.Properties.Name -contains 'processes') {
        $records += @($State.processes)
    }
    $managed = @{}
    foreach ($record in $records) {
        if ($managed.ContainsKey([int]$record.pid)) { continue }
        $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if (-not $process) { continue }
        if (-not (Test-AppIdentity $process $record)) { throw 'Process identity changed. Refusing to control it.' }
        $info = Get-CimInstance Win32_Process -Filter "ProcessId = $($process.Id)"
        if (-not $info -or -not (Test-AppServerCommand $info)) { throw 'App command line does not match this workspace. Refusing to control it.' }
        $null = $process.SafeHandle
        $managed[$process.Id] = [pscustomobject]@{ process=$process; info=$info; depth=0; started_at=$process.StartTime.ToUniversalTime(); executable=$process.Path }
    }
    # CPython's Windows venv executable can be a launcher with a separate server child.
    # Discover only descendants of verified app processes; never enumerate/kill by name.
    $queue = [Collections.Generic.Queue[object]]::new()
    foreach ($item in @($managed.Values)) { $queue.Enqueue($item) }
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        foreach ($info in @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($parent.process.Id)")) {
            if ($managed.ContainsKey([int]$info.ProcessId)) { continue }
            # Windows owns the hidden console host; do not terminate system helpers.
            if ($info.ExecutablePath -and [IO.Path]::GetFullPath($info.ExecutablePath) -eq (Join-Path $env:SystemRoot 'System32\conhost.exe')) { continue }
            $child = Get-Process -Id $info.ProcessId -ErrorAction SilentlyContinue
            if (-not $child) { continue }
            # Windows may reuse a parent PID referenced by an older, unrelated
            # process. Such a process cannot be a child of this app instance.
            if ($child.StartTime.ToUniversalTime() -lt $parent.process.StartTime.ToUniversalTime()) { continue }
            $isServer = Test-AppServerCommand $info
            $isWorker = $info.ExecutablePath -and [IO.Path]::GetFullPath($info.ExecutablePath) -in (Get-AppPythonExecutables) -and
                $info.CommandLine -match 'from multiprocessing\.spawn import spawn_main' -and
                $info.CommandLine -match ('parent_pid\s*=\s*' + $parent.process.Id + '(?:\s*[,\)])') -and
                $info.CommandLine -match '--multiprocessing-fork'
            if (-not $isServer -and -not $isWorker) {
                throw 'An unrecognized child process is attached to the app. Refusing a partial shutdown.'
            }
            $null = $child.SafeHandle
            $item = [pscustomobject]@{process=$child; info=$info; depth=($parent.depth+1); started_at=$child.StartTime.ToUniversalTime(); executable=$child.Path}
            $managed[$child.Id]=$item
            $queue.Enqueue($item)
        }
    }
    return @($managed.Values)
}

function Write-AppRuntime([Diagnostics.Process]$Process) {
    $state = [pscustomobject]@{pid=$Process.Id; started_at=$Process.StartTime.ToUniversalTime().ToString('o'); root=$ProjectRoot; executable=$Process.Path}
    $tracked = @(Get-ManagedAppProcesses $state | Where-Object { Test-AppServerCommand $_.info } | ForEach-Object {
        @{pid=$_.process.Id; started_at=$_.process.StartTime.ToUniversalTime().ToString('o'); executable=$_.process.Path}
    })
    $state | Add-Member -NotePropertyName processes -NotePropertyValue $tracked
    [IO.File]::WriteAllText($statePath, ($state | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}
