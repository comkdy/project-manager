param()
. (Join-Path $PSScriptRoot 'database-common.ps1')
$runtime = Get-DemoRuntime
$pgCtl = Join-Path $runtime.bin_dir 'pg_ctl.exe'
& $pgCtl -D $runtime.data_dir status *> $null
if ($LASTEXITCODE -eq 0) { Write-Host 'Workspace PostgreSQL is already running on 127.0.0.1:55432.'; exit 0 }
$arguments = @('-D', ('"{0}"' -f $runtime.data_dir), '-l', ('"{0}"' -f (Join-Path $LocalRoot 'postgresql.log')), '-w', '-t', '30', 'start')
$process = Start-Process -FilePath $pgCtl -ArgumentList $arguments -WindowStyle Hidden -PassThru
# Start-Process -Wait waits for descendants too; PostgreSQL intentionally stays running.
if (-not $process.WaitForExit(45000)) { throw 'PostgreSQL launcher timed out. Inspect .local/postgresql.log.' }
if ($process.ExitCode -ne 0) { throw 'PostgreSQL failed to start. Inspect .local/postgresql.log.' }
Write-Host 'Workspace PostgreSQL is running on 127.0.0.1:55432.'
