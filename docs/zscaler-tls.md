# Zscaler + packman/Kit build on Windows

Zscaler TLS-inspects HTTPS, so packman's Python downloader cannot chain to a
trusted root:

```
SSLCertVerificationError: unable to get local issuer certificate
```

Python reads `REQUESTS_CA_BUNDLE` (requests) and `SSL_CERT_FILE` (urllib/ssl) at
import time. Point both at a PEM containing your Zscaler root **plus** the
normal public roots, then retry the build.

## 1. Export the Zscaler root CA

Zscaler intercepts with its own CA. Find it in Windows' trusted store, or ask
IT — it is normally one of:

```
C:\ProgramData\Zscaler\<something>\Zscaler Root CA.crt
C:\ProgramData\Microsoft\Crypto\RSA\<...>.cer   (may already be in the store)
```

PowerShell — list every root Windows trusts, which will include it if IT has
installed it:

```powershell
Get-ChildItem Cert:\LocalMachine\Root | Where-Object { $_.Subject -like "*Zscaler*" } |
    Select-Object Subject, Thumbprint, NotAfter
```

Export the one you find (pick `LocalMachine`; `CurrentUser` also works):

```powershell
$zs = Get-ChildItem Cert:\LocalMachine\Root |
      Where-Object { $_.Subject -like "*Zscaler*" } |
      Select-Object -First 1

"exporting: " + $zs.Subject
Export-Certificate -Cert $zs -FilePath C:\certs\zscaler-root.pem -Type CERT
```

If that finds nothing, Zscaler is installed per-user:

```powershell
Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -like "*Zscaler*" }
```

## 2. Build a combined bundle

Do **not** replace the public roots — packman fetches from
`bootstrap.packman.nvidia.com` and `d4i3qtqj3r0z5.cloudfront.net`, and you
still need to verify those. Concatenate Zscaler *and* the Python-bundled
certifi set, so both your proxy and the public chain validate:

```powershell
New-Item -ItemType Directory -Force C:\certs | Out-Null

# the public roots Python ships with (certifi)
& .\.packman-repo\python\python.exe -c "import certifi; print(certifi.where())"
# -> copy that .pem next to your Zscaler export, e.g. C:\certs\public-roots.pem

Get-Content C:\certs\zscaler-root.pem, C:\certs\public-roots.pem `
  -Encoding UTF8 | Set-Content C:\certs\packman-ca-bundle.pem -Encoding UTF8

"bundle written: " + (Get-Item C:\certs\packman-ca-bundle.pem).Length + " bytes"
```

If `certifi` is not importable yet (the build has not installed deps), use the
Windows store instead, which is enough on its own because it already contains
Zscaler:

```powershell
$store = Get-ChildItem Cert:\LocalMachine\Root
$store | ForEach-Object { Export-Certificate -Cert $_ -FilePath `
             ("C:\certs\root-{0}.cer" -f $_.Thumbprint) -Type CERT | Out-Null }
Get-ChildItem C:\certs\root-*.cer | ForEach-Object {
    Get-Content $_.FullName -Encoding UTF8 |
        Set-Content ("C:\certs\{0}.pem" -f $_.BaseName) -Encoding UTF8 }
Get-Content C:\certs\root-*.pem -Encoding UTF8 |
    Set-Content C:\certs\packman-ca-bundle.pem -Encoding UTF8
```

## 3. Point the build at it, then retry

Niko's bundle is already at `C:\Users\NRaes\.ca-bundle.pem`, so this is the
whole fix:

```powershell
$env:REQUESTS_CA_BUNDLE = "C:\Users\NRaes\.ca-bundle.pem"
$env:SSL_CERT_FILE     = "C:\Users\NRaes\.ca-bundle.pem"

.\repo.bat build
```

Packman's own `curl` fetches need curl's variable as well:

```powershell
$env:CURL_CA_BUNDLE = "C:\Users\NRaes\.ca-bundle.pem"
```

If you built the bundle from scratch elsewhere, substitute that path in the
three lines above.

If packman shells out to `curl` for some packages, add curl's equivalent too:

```powershell
$env:CURL_CA_BUNDLE = "C:\certs\packman-ca-bundle.pem"
```

## 4. Make it stick

Per-user, so every future `repo.bat` picks it up:

```powershell
[Environment]::SetEnvironmentVariable("REQUESTS_CA_BUNDLE", "C:\Users\NRaes\.ca-bundle.pem", "User")
[Environment]::SetEnvironmentVariable("SSL_CERT_FILE",     "C:\Users\NRaes\.ca-bundle.pem", "User")
[Environment]::SetEnvironmentVariable("CURL_CA_BUNDLE",    "C:\Users\NRaes\.ca-bundle.pem", "User")
```

Open a new terminal afterwards — the running one is unaffected.

## 5. Verify before trusting it

```powershell
& .\.packman-repo\python\python.exe -c "import requests,urllib.request;
print(requests.get('https://d4i3qtqj3r0z5.cloudfront.net/', timeout=30).status_code)"
```

A `200`/`403` means the TLS chain validated (the URL answered; it just has no
root page). An `SSLCertVerificationError` means the bundle is still missing the
Zscaler root — go back to step 1 and confirm `Subject` actually matches.

## Note

If Zscaler is installed as an `.exe`/root in the Windows store and Python *still*
fails while `curl` succeeds, the simplest escape is to pre-download the handful of
archives packman wants into `C:\packman-repo` and skip its HTTPS path — but try
the bundle first, since it keeps the whole toolchain working.