param([switch]$NoBuild, [switch]$SkipDatabase)
. (Join-Path $PSScriptRoot 'app-process-common.ps1')
$python = $AppPython
if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv and install backend first. See README.md.' }
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot '.env'))) { throw 'Configure .env or run scripts/setup-database.ps1 first.' }
if (-not $SkipDatabase) { & (Join-Path $PSScriptRoot 'start-database.ps1') }
if (Test-Path -LiteralPath $statePath) {
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    $existing = @(Get-ManagedAppProcesses $state)
    if ($existing.Count -gt 0) {
        Write-Host 'Project Manager is already running at http://127.0.0.1:8000'
        exit 0
    }
}
if (-not $NoBuild) {
    $nodeCommand = Get-Command node -ErrorAction Stop
    $pnpmCommand = Get-Command pnpm.cmd -ErrorAction SilentlyContinue
    $nodeParent = Split-Path $nodeCommand.Source -Parent
    $pnpmCli = @((Join-Path $nodeParent 'node_modules\pnpm\bin\pnpm.cjs'), (Join-Path (Split-Path $nodeParent -Parent) 'node_modules\pnpm\bin\pnpm.cjs')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    Push-Location (Join-Path $ProjectRoot 'frontend')
    try {
        if (-not (Test-Path -LiteralPath 'node_modules')) {
            if ($pnpmCommand) { & $pnpmCommand.Source install --frozen-lockfile }
            elseif ($pnpmCli) { & $nodeCommand.Source $pnpmCli install --frozen-lockfile }
            else { throw 'pnpm was not found. Install pnpm, then rerun this script.' }
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        }
        & $nodeCommand.Source 'node_modules\typescript\bin\tsc' -b
        if ($LASTEXITCODE -ne 0) { throw 'Frontend type checking failed.' }
        & $nodeCommand.Source 'node_modules\vite\bin\vite.js' build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot 'frontend\dist\index.html'))) { throw 'Build frontend before using -NoBuild.' }
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) { throw 'Port 8000 is already in use by an unmanaged process. No process was stopped.' }
New-Item -ItemType Directory -Force -Path $LocalRoot | Out-Null
$backend = Join-Path $ProjectRoot 'backend'
$arguments = @('-m', 'uvicorn', 'app.main:app', '--app-dir', ('"{0}"' -f $backend), '--host', '127.0.0.1', '--port', '8000')
$process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $LocalRoot 'app.stdout.log') -RedirectStandardError (Join-Path $LocalRoot 'app.stderr.log')
Write-AppRuntime $process
for ($attempt=0; $attempt -lt 40; $attempt++) {
    Start-Sleep -Milliseconds 500
    $process.Refresh()
    if ($process.HasExited) { throw 'App exited during startup. See .local/app.stderr.log.' }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
        if ($response.StatusCode -eq 200) { Write-AppRuntime $process; Write-Host 'Project Manager: http://127.0.0.1:8000'; exit 0 }
    } catch { }
}
throw 'App startup did not finish in time. See .local/app.stderr.log.'
