@echo off
REM Install the packman pre-seed that sidesteps Zscaler's .7z-from-CDN block.
REM
REM The logic lives in seed_packman.py, not here and not in a .ps1:
REM
REM   * PowerShell blocks .ps1 on this machine (PSSecurityException)
REM   * and its Get-FileHash is missing too, so even bypassing the policy leaves
REM     the script depending on cmdlets this shell does not have
REM
REM Python is already present and working (uv resolves boto3 in under a second),
REM so the .cmd is just a launcher. One implementation, no shell dialect
REM lottery.
REM
REM Credentials come from %APPDATA%\renderq\renderq.env, the same file the
REM render queue uses.

setlocal
cd /d "%~dp0.."

uv run --with boto3 python "%~dp0seed_packman.py" %*
exit /b %errorlevel%
