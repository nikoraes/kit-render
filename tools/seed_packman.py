#!/usr/bin/env python3
"""Install the packman pre-seed that sidesteps Zscaler's .7z-from-CDN block.

WHY THIS IS PYTHON, NOT POWERSHELL
----------------------------------
The PowerShell version failed on this machine with:

    Get-FileHash : The term 'Get-FileHash' is not recognized ...

so the shell is locked down (constrained language mode, or an old PowerShell).
Any non-core cmdlet is a liability there. Python is already present and working
(uv resolves boto3 in under a second), so all the logic lives here and the
.cmd is a one-line launcher. One source of truth instead of a shell dialect
lottery.

WHAT IT FIXES
-------------
Zscaler blocks `.7z` downloads from CDN-categorised hosts, and packman needs
nine of them from d4i3qtqj3r0z5.cloudfront.net. It surfaces one per build, each
costing a full build cycle:

    PackmanError: Unable to download file from ... - 403 Forbidden

packman skips any package already installed:

    status, install_path = packager.get_package_install_info(name, version, ...)
    if status != packager.STATUS_INSTALLED:
        ...download...

so unpacking each package into <PM_PACKAGES_ROOT>/chk/<name>/<version>/ with a
.packman.sha1 marker makes packman treat it as present and never request it.
Verified against packman's own code: an empty root reports status=2 (MISSING),
a seeded one status=0 (INSTALLED).

Garage is on the LAN, so this download is not proxied.

This does NOT cover the extension stack -- that is fetched by Kit's registry
client, which is configured separately in repo.toml
(kit_use_urllib_for_http = true).
"""
from __future__ import annotations

import hashlib
import os
import shutil
import sys
import tarfile
import tempfile
from typing import NoReturn

BUCKET_DEFAULT = "render"
KEY = "packman-preseed/packman-seed-v3.tar.gz"
EXPECTED_SHA256 = "7acb375b3ee99c9a0535c098a848a09bd2ea02b62bf6d621f093473eaceb9c60"

PACKAGES_ROOT_DEFAULT = r"C:\packman-repo"
ENDPOINT_DEFAULT = "https://s3.local.raes.konnektr.io"
REGION_DEFAULT = "us-east-1"


def die(msg: str) -> NoReturn:
    print("\nERROR: %s" % msg, file=sys.stderr)
    raise SystemExit(1)


def read_env_file() -> dict:
    """Read the render queue's credentials, the same file it uses itself."""
    appdata = os.environ.get("APPDATA", "")
    path = os.path.join(appdata, "renderq", "renderq.env")
    creds: dict[str, str] = {}
    if os.path.isfile(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
        print("  credentials: %s" % path)
    else:
        print("  credentials: %s not found, falling back to environment" % path)
    return creds


def cred(creds: dict, key: str, fallback: str | None = None) -> str:
    v = creds.get(key) or os.environ.get(key) or fallback
    if not v:
        die("%s is not set (checked renderq.env and the environment)" % key)
    return v


def download(dest: str) -> None:
    import botocore.config
    import boto3

    creds = read_env_file()
    endpoint = cred(creds, "RENDERQ_ENDPOINT", ENDPOINT_DEFAULT)
    region = cred(creds, "RENDERQ_REGION", REGION_DEFAULT)
    bucket = cred(creds, "RENDERQ_BUCKET", BUCKET_DEFAULT)
    ak = cred(creds, "RENDERQ_ACCESS_KEY")

    print("Fetching seed from %s (bucket %s) ..." % (endpoint, bucket))

    # endpoint_url must be passed explicitly: AWS_ENDPOINT_URL is ignored by some
    # botocore versions, which silently sends the request to real AWS and yields
    # "400 Bad Request" from HeadObject. Garage also needs path-style addressing.
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=ak,
        aws_secret_access_key=cred(creds, "RENDERQ_SECRET_KEY"),
        config=botocore.config.Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 5, "mode": "standard"},
            request_checksum_calculation="when_required",
            response_checksum_validation="when_required",
        ),
    )
    s3.download_file(bucket, KEY, dest)
    print("  downloaded %d bytes" % os.path.getsize(dest))


def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    root = os.environ.get("PM_PACKAGES_ROOT") or PACKAGES_ROOT_DEFAULT
    work = tempfile.mkdtemp(prefix="packman-preseed-")
    tarball = os.path.join(work, "seed.tar.gz")

    try:
        download(tarball)

        print("Verifying sha256 ...")
        actual = sha256_of(tarball)
        if actual != EXPECTED_SHA256:
            die("sha256 mismatch\n  expected %s\n  actual   %s"
                % (EXPECTED_SHA256, actual))
        print("  sha256 OK")

        print("Extracting ...")
        with tarfile.open(tarball, "r:gz") as tf:
            # filter="data" refuses absolute paths, links outside the tree and
            # device nodes. Also silences the Python 3.14 deprecation warning.
            tf.extractall(work, filter="data")

        src = os.path.join(work, "chk")
        if not os.path.isdir(src):
            die("extracted tree has no chk/ directory -- wrong archive layout")

        dst = os.path.join(root, "chk")
        os.makedirs(dst, exist_ok=True)
        for name in sorted(os.listdir(src)):
            s = os.path.join(src, name)
            d = os.path.join(dst, name)
            if os.path.isdir(d):
                shutil.rmtree(d)
            shutil.copytree(s, d)
        print("  installed into %s" % dst)

        print()
        print("Seeded packages:")
        for name in sorted(os.listdir(dst)):
            pkg = os.path.join(dst, name)
            if not os.path.isdir(pkg):
                continue
            vers = [v for v in os.listdir(pkg)
                    if os.path.isdir(os.path.join(pkg, v))]
            ver = vers[0] if vers else "?"
            marker = os.path.isfile(os.path.join(pkg, ver, ".packman.sha1"))
            nfiles = sum(len(f) for _, _, f in os.walk(os.path.join(pkg, ver)))
            print("  %-22s %-34s %5d files  marker=%s"
                  % (name, ver, nfiles, marker))

        print()
        print("Done. Now run:  .\\repo.bat build")
        return 0
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
