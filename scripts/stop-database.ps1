param()
. (Join-Path $PSScriptRoot 'database-common.ps1')
$runtime = Get-DemoRuntime
$pgCtl = Join-Path $runtime.bin_dir 'pg_ctl.exe'
& $pgCtl -D $runtime.data_dir status *> $null
if ($LASTEXITCODE -ne 0) { Write-Host 'Workspace PostgreSQL is already stopped.'; exit 0 }
$arguments = @('-D', ('"{0}"' -f $runtime.data_dir), '-m', 'fast', '-w', '-t', '30', 'stop')
$process = Start-Process -FilePath $pgCtl -ArgumentList $arguments -WindowStyle Hidden -PassThru
if (-not $process.WaitForExit(45000)) { throw 'PostgreSQL stop launcher timed out.' }
if ($process.ExitCode -ne 0) { throw 'Could not stop workspace PostgreSQL.' }
Write-Host 'Workspace PostgreSQL stopped.'
