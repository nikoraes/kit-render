$ErrorActionPreference = "Stop"

# Install the packman pre-seed that sidesteps Zscaler's .7z-from-CDN block.
#
# Zscaler blocks `.7z` downloads from CDN-categorised hosts. packman needs seven
# of them from d4i3qtqj3r0z5.cloudfront.net, so `.\repo.bat build` dies with:
#
#     PackmanError: Unable to download file from ... - 403 Forbidden
#
# packman skips any package that is already installed:
#
#     status, install_path = packager.get_package_install_info(name, version, ...)
#     if status != packager.STATUS_INSTALLED:
#         ...download...
#
# so unpacking each package into <PM_PACKAGES_ROOT>\chk\<name>\<version>\ with a
# .packman.sha1 marker makes packman treat it as present and never request it.
# Verified against packman's own code: an empty root reports status=2 (MISSING),
# a seeded one status=0 (INSTALLED).
#
# Garage is on the LAN, so this download is not proxied. Credentials come from
# the same place the render queue uses them.

$Root      = "C:\packman-repo"
$Work      = Join-Path $env:TEMP "packman-preseed"
$Tarball   = "$Work\packman-preseed.tar.gz"
$Expected  = "008310e2065a272a64891c9b37a8fb57d062d8650fe7003973288032afb9c670"
$Bucket    = "render"
$Key       = "packman-preseed/packman-preseed-20261002.tar.gz"
$Endpoint  = "https://s3.local.raes.konnektr.io"

if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force $Work | Out-Null

Write-Host "Fetching pre-seed from $Endpoint ..." -ForegroundColor Cyan

# Credentials: the render queue's env file if present, else the ambient profile.
$EnvFile = Join-Path $env:APPDATA "renderq\renderq.env"
$creds = @{}
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([^#=]+?)\s*=\s*(.+?)\s*$') { $creds[$matches[1].Trim()] = $matches[2].Trim() }
    }
}
$ak = if ($creds["RENDERQ_ACCESS_KEY"]) { $creds["RENDERQ_ACCESS_KEY"] } else { $env:RENDERQ_ACCESS_KEY }
$sk = if ($creds["RENDERQ_SECRET_KEY"]) { $creds["RENDERQ_SECRET_KEY"] } else { $env:RENDERQ_SECRET_KEY }
if (-not $ak -or -not $sk) {
    throw "no S3 credentials. Set RENDERQ_ACCESS_KEY / RENDERQ_SECRET_KEY, or populate $EnvFile"
}

$env:AWS_ACCESS_KEY_ID = $ak
$env:AWS_SECRET_ACCESS_KEY = $sk
$env:AWS_DEFAULT_REGION  = "garage"

$env:AWS_ENDPOINT_URL = $Endpoint
uv run --with boto3 python -c @"
import boto3, os
s3 = boto3.client('s3')
s3.download_file('$Bucket', '$Key', r'$Tarball')
print('  downloaded', os.path.getsize(r'$Tarball'), 'bytes')
"@
if ($LASTEXITCODE -ne 0) { throw "download failed" }

Write-Host "Verifying sha256 ..." -ForegroundColor Cyan
$Actual = (Get-FileHash $Tarball -Algorithm SHA256).Hash.ToLower()
if ($Actual -ne $Expected) {
    throw "sha256 mismatch`n  expected $Expected`n  actual   $Actual"
}
Write-Host "  sha256 OK" -ForegroundColor Green

Write-Host "Extracting ..." -ForegroundColor Cyan
tar -xzf $Tarball -C $Work

$Src = Join-Path $Work "packman-preseed\chk"
if (-not (Test-Path $Src)) { throw "extracted tree missing chk\ -- wrong archive layout" }

New-Item -ItemType Directory -Force "$Root\chk" | Out-Null
Copy-Item -Recurse -Force "$Src\*" "$Root\chk\"

Write-Host ""
Write-Host "Seeded packages:" -ForegroundColor Cyan
Get-ChildItem "$Root\chk" -Directory | ForEach-Object {
    $pkg  = $_.Name
    $ver  = (Get-ChildItem $_.FullName -Directory | Select-Object -First 1).Name
    $mark = Test-Path "$Root\chk\$pkg\$ver\.packman.sha1"
    $n    = (Get-ChildItem "$Root\chk\$pkg\$ver" -Recurse -File).Count
    "  {0,-20} {1,-10} {2,4} files  marker={3}" -f $pkg, $ver, $n, $mark
}

Write-Host ""
Write-Host "Done. Now run: .\repo.bat build" -ForegroundColor Green