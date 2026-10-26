param([switch]$SkipDownload)
. (Join-Path $PSScriptRoot 'database-common.ps1')
New-Item -ItemType Directory -Force -Path $LocalRoot | Out-Null
$archive = Join-Path $LocalRoot 'postgresql.zip'
$download = 'https://get.enterprisedb.com/postgresql/postgresql-17.11-3-windows-x64-binaries.zip'
# Vendor-published digest: https://github.com/EnterpriseDB/edb-installers/issues/682
$expectedHash = '4b8db0930c38f6ef845db919551dedda3b6b845aeb0927b3d79a6e8e9e4537cf'
$bin = Join-Path $LocalRoot 'pgsql\bin'
function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($bytes) } finally { $rng.Dispose() }
    return [BitConverter]::ToString($bytes).Replace('-','').ToLowerInvariant()
}
if (-not (Test-Path -LiteralPath (Join-Path $LocalRoot 'pgsql\.wafer-runtime-complete'))) {
    if (-not (Test-Path -LiteralPath $archive)) {
        if ($SkipDownload) { throw 'Missing .local/postgresql.zip.' }
        Write-Host 'Downloading portable PostgreSQL from EDB...'
        Invoke-WebRequest -Uri $download -OutFile $archive
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw 'PostgreSQL archive SHA-256 does not match the vendor checksum.'
    }
    Write-Host 'Extracting PostgreSQL into this workspace...'
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            # Server runtime only: do not unpack the bundled pgAdmin and development tools.
            if ($entry.FullName -notmatch '^pgsql/((bin|lib|share)/|server_license\.txt$|commandlinetools_3rd_party_licenses\.txt$)' -or $entry.Name -eq '') { continue }
            $destination = [IO.Path]::GetFullPath((Join-Path $LocalRoot $entry.FullName))
            if (-not $destination.StartsWith($LocalRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
                throw 'Archive path is outside the local runtime directory.'
            }
            [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($destination)) | Out-Null
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $destination, $true)
        }
    } finally { $zip.Dispose() }
    'verified-runtime' | Set-Content -LiteralPath (Join-Path $LocalRoot 'pgsql\.wafer-runtime-complete')
}
if (-not (Test-Path -LiteralPath $RuntimePath)) {
    if (Test-Path -LiteralPath (Join-Path $LocalRoot 'pgdata')) { throw 'An unrecognized pgdata directory exists. Refusing to replace it.' }
    $runtime = [ordered]@{
        marker = 'wafer-local-demo-v1'; port = 55432; bin_dir = $bin
        data_dir = (Join-Path $LocalRoot 'pgdata'); admin_password = (New-Secret)
        source_password = (New-Secret); app_password = (New-Secret)
    }
    $runtime | ConvertTo-Json | Set-Content -LiteralPath $RuntimePath -Encoding utf8
}
$runtime = Get-DemoRuntime
if (-not (Test-Path -LiteralPath (Join-Path $runtime.data_dir 'PG_VERSION'))) {
    $pwfile = Join-Path $LocalRoot 'initdb-password.tmp'
    try {
        [IO.File]::WriteAllText($pwfile, $runtime.admin_password, [Text.UTF8Encoding]::new($false))
        & (Join-Path $bin 'initdb.exe') -D $runtime.data_dir -U wafer_local_admin -E UTF8 --locale=C --auth=scram-sha-256 "--pwfile=$pwfile"
        if ($LASTEXITCODE -ne 0) { throw 'initdb failed.' }
    } finally { if (Test-Path -LiteralPath $pwfile) { Remove-Item -LiteralPath $pwfile } }
    @"
listen_addresses = '127.0.0.1'
port = 55432
max_connections = 40
shared_buffers = '128MB'
timezone = 'UTC'
log_statement = 'none'
"@ | Add-Content -LiteralPath (Join-Path $runtime.data_dir 'postgresql.conf')
}
& (Join-Path $PSScriptRoot 'start-database.ps1')
$roleSql = @"
SELECT 'CREATE ROLE wafer_source LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='wafer_source') \gexec
SELECT 'CREATE ROLE wafer_metadata LOGIN' WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='wafer_metadata') \gexec
ALTER ROLE wafer_source PASSWORD '$($runtime.source_password)';
ALTER ROLE wafer_metadata PASSWORD '$($runtime.app_password)';
ALTER ROLE wafer_source SET default_transaction_read_only=on;
ALTER ROLE wafer_source NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
SELECT 'CREATE DATABASE wafer_demo OWNER wafer_local_admin' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='wafer_demo') \gexec
SELECT 'CREATE DATABASE wafer_app OWNER wafer_metadata' WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='wafer_app') \gexec
REVOKE ALL ON DATABASE wafer_demo FROM PUBLIC;
REVOKE ALL ON DATABASE wafer_app FROM PUBLIC;
GRANT CONNECT ON DATABASE wafer_demo TO wafer_source;
REVOKE CREATE,TEMPORARY ON DATABASE wafer_demo FROM wafer_source;
GRANT CONNECT ON DATABASE wafer_app TO wafer_metadata;
"@
Invoke-DemoSql $runtime 'postgres' $roleSql
Invoke-DemoSql $runtime 'wafer_demo' @'
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE CREATE ON SCHEMA public FROM wafer_source;
GRANT USAGE ON SCHEMA public TO wafer_source;
REVOKE INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER ON ALL TABLES IN SCHEMA public FROM wafer_source;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM wafer_source;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO wafer_source;
ALTER DEFAULT PRIVILEGES FOR ROLE wafer_local_admin IN SCHEMA public GRANT SELECT ON TABLES TO wafer_source;
CREATE TABLE IF NOT EXISTS public.demo_identity (marker text PRIMARY KEY CHECK (marker='wafer-local-demo-v1'));
INSERT INTO public.demo_identity VALUES ('wafer-local-demo-v1') ON CONFLICT DO NOTHING;
'@
Invoke-DemoSql $runtime 'wafer_app' 'REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT ALL ON SCHEMA public TO wafer_metadata;'
$envPath = Join-Path $ProjectRoot '.env'
$lines = @()
if (Test-Path -LiteralPath $envPath) { $lines = @(Get-Content -LiteralPath $envPath | Where-Object { $_ -notmatch '^(APP_DATABASE_URL|SOURCE_DATABASE_URL|TABLE_IGNORE|DEMO_MODE|BOOTSTRAP_ADMIN_PASSWORD|BOOTSTRAP_EDITOR_PASSWORD|BOOTSTRAP_VIEWER_PASSWORD)=' }) }
else { $lines = @(Get-Content -LiteralPath (Join-Path $ProjectRoot '.env.example') | Where-Object { $_ -notmatch '^(APP_DATABASE_URL|DEMO_MODE|AUTH_MODE)=' }) }
$lines += "APP_DATABASE_URL=postgresql+psycopg://wafer_metadata:$($runtime.app_password)@127.0.0.1:55432/wafer_app"
$lines += 'DEMO_MODE=true'
if (-not ($lines | Where-Object { $_ -match '^AUTH_MODE=' })) { $lines += 'AUTH_MODE=local_admin' }
[IO.File]::WriteAllLines($envPath, [string[]]$lines, [Text.UTF8Encoding]::new($false))
Write-Host 'Local databases configured. Run scripts/seed-demo.ps1 to populate and register the demo source. Analysis DB connections are managed in the web DB settings.'
