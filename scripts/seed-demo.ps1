param([ValidateSet('dev','perf')][string]$Profile='dev', [int]$Seed=20261019, [switch]$Replace)
. (Join-Path $PSScriptRoot 'database-common.ps1')
$runtime=Get-DemoRuntime
$python=Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Create .venv and install backend/requirements.txt first.' }
$arguments=@((Join-Path $ProjectRoot 'demo\seed.py'),'--profile',$Profile,'--seed',$Seed)
if ($Replace) { $arguments+='--replace' }
& $python @arguments
if ($LASTEXITCODE -ne 0) { throw 'Demo seed failed.' }
& $python (Join-Path $ProjectRoot 'scripts\register-demo.py')
if ($LASTEXITCODE -ne 0) { throw 'Demo source registration failed.' }
