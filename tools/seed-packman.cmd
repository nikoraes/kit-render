@echo off
REM Wrapper so the seed runs without changing the machine's execution policy.
REM
REM PowerShell blocks .ps1 on this machine (PSSecurityException). Three ways
REM around it, in order of preference:
REM
REM   1. this wrapper -- it calls powershell -ExecutionPolicy Bypass for one
REM      process only, which does not alter the machine or user policy
REM   2. Unblock-File, if you prefer to run the .ps1 directly:
REM         Unblock-File .\tools\seed-packman.ps1
REM   3. RemoteSigned for your user only (needs no admin):
REM         Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
REM
REM Option 1 is the default here because it leaves no trace on the machine.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0seed-packman.ps1" %*
exit /b %errorlevel%