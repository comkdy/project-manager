$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$LocalRoot = Join-Path $ProjectRoot '.local'
$RuntimePath = Join-Path $LocalRoot 'runtime.json'

function Get-DemoRuntime {
    if (-not (Test-Path -LiteralPath $RuntimePath)) { throw 'Run scripts/setup-database.ps1 first.' }
    $runtime = Get-Content -LiteralPath $RuntimePath -Raw | ConvertFrom-Json
    $expected = [IO.Path]::GetFullPath((Join-Path $LocalRoot 'pgdata'))
    $expectedBin = [IO.Path]::GetFullPath((Join-Path $LocalRoot 'pgsql\bin'))
    if ([IO.Path]::GetFullPath($runtime.data_dir) -ne $expected -or [IO.Path]::GetFullPath($runtime.bin_dir) -ne $expectedBin -or $runtime.port -ne 55432 -or $runtime.marker -ne 'wafer-local-demo-v1') {
        throw 'Runtime does not identify this workspace demo. Refusing to control an unrelated PostgreSQL instance.'
    }
    return $runtime
}

function Invoke-DemoSql([object]$Runtime, [string]$Database, [string]$Sql) {
    $previous = $env:PGPASSWORD
    try {
        $env:PGPASSWORD = $Runtime.admin_password
        $Sql | & (Join-Path $Runtime.bin_dir 'psql.exe') -X -q -v ON_ERROR_STOP=1 -h 127.0.0.1 -p $Runtime.port -U wafer_local_admin -d $Database
        if ($LASTEXITCODE -ne 0) { throw "PostgreSQL command failed for $Database." }
    } finally { $env:PGPASSWORD = $previous }
}
