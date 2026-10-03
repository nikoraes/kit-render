$ErrorActionPreference = "Stop"

# Install the packman pre-seed that sidesteps Zscaler's .7z-from-CDN block.
#
# Zscaler blocks `.7z` downloads from CDN-categorised hosts. The build needs
# nine of them from d4i3qtqj3r0z5.cloudfront.net, so `.
epo.bat build` dies with:
#
#     PackmanError: Unable to download file from ... - 403 Forbidden
#
# It surfaces one at a time, each costing a full build cycle: first the seven
# repo tools (repo_man, repo_build, repo_kit_tools, repo_test, repo_package,
# repo_kit_template, repo_usd), then the kit-sdk deps (boost-preprocessor,
# pybind11). Both batches are in this one tarball.
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
# Garage is on the LAN, so this download is not proxied.

$Root     = "C:\packman-repo"
$Work     = Join-Path $env:TEMP "packman-preseed"
$Tarball  = "$Work\packman-preseed.tar.gz"
$Expected = "7710a3b350a9f9558c605c13b3d50a3df5cb526811c2cab944d1f65d2c49decb"

if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force $Work | Out-Null

# ---- credentials -----------------------------------------------------------
# Same env file the render queue uses. Garage needs path-style addressing and
# s3v4, and the endpoint must be passed as endpoint_url= -- AWS_ENDPOINT_URL is
# ignored by some botocore versions, which silently sends the request to real
# AWS and yields a 400 from HeadObject.
$EnvFile = Join-Path $env:APPDATA "renderq\renderq.env"
$creds = @{}
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -match '^\s*([^#=][^=]*?)\s*=\s*(.+?)\s*$') { $creds[$matches[1].Trim()] = $matches[2].Trim() }
    }
}
$ak = if ($creds["RENDERQ_ACCESS_KEY"]) { $creds["RENDERQ_ACCESS_KEY"] } else { $env:RENDERQ_ACCESS_KEY }
$sk = if ($creds["RENDERQ_SECRET_KEY"]) { $creds["RENDERQ_SECRET_KEY"] } else { $env:RENDERQ_SECRET_KEY }
$ep = if ($creds["RENDERQ_ENDPOINT"])  { $creds["RENDERQ_ENDPOINT"]  } else { $env:RENDERQ_ENDPOINT  }
$rg = if ($creds["RENDERQ_REGION"])    { $creds["RENDERQ_REGION"]    } else { $env:RENDERQ_REGION    }
$bk = if ($creds["RENDERQ_BUCKET"])    { $creds["RENDERQ_BUCKET"]    } else { $env:RENDERQ_BUCKET    }

if (-not $ak -or -not $sk -or -not $ep) {
    throw "no S3 credentials. Populate $EnvFile with RENDERQ_ACCESS_KEY / RENDERQ_SECRET_KEY / RENDERQ_ENDPOINT"
}
if (-not $rg) { $rg = "garage" }
if (-not $bk) { $bk = "render" }

Write-Host "Fetching pre-seed from $ep (bucket $bk) ..." -ForegroundColor Cyan

# Write the downloader to a file rather than inlining it: uv's python -c with a
# here-string mangles quoting of the r'' paths.
$Py = Join-Path $Work "_dl.py"
@"
import os, sys
import botocore.config
import boto3

s3 = boto3.client(
    "s3",
    endpoint_url=os.environ["S3_ENDPOINT"],
    region_name=os.environ["S3_REGION"],
    aws_access_key_id=os.environ["S3_AK"],
    aws_secret_access_key=os.environ["S3_SK"],
    config=botocore.config.Config(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        retries={"max_attempts": 5, "mode": "standard"},
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    ),
)
bucket, key, dest = sys.argv[1], sys.argv[2], sys.argv[3]
s3.download_file(bucket, key, dest)
print("  downloaded", os.path.getsize(dest), "bytes")
"@ | Set-Content -Path $Py -Encoding UTF8

$env:S3_ENDPOINT = $ep
$env:S3_REGION   = $rg
$env:S3_AK       = $ak
$env:S3_SK       = $sk

uv run --with boto3 python $Py $bk "packman-preseed/packman-seed-v2.tar.gz" $Tarball
if ($LASTEXITCODE -ne 0) { throw "download failed" }

# ---- verify, then install --------------------------------------------------
Write-Host "Verifying sha256 ..." -ForegroundColor Cyan
$Actual = (Get-FileHash $Tarball -Algorithm SHA256).Hash.ToLower()
if ($Actual -ne $Expected) {
    throw "sha256 mismatch`n  expected $Expected`n  actual   $Actual"
}
Write-Host "  sha256 OK" -ForegroundColor Green

Write-Host "Extracting ..." -ForegroundColor Cyan
tar -xzf $Tarball -C $Work

$Src = Join-Path $Work "chk"
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