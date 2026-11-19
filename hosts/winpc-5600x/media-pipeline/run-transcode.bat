@echo off
REM Canonical source is gjcourt/homelab hosts/winpc-5600x/media-pipeline/.
REM This box is NOT authoritative: it pulls before every run, so a local edit
REM here is silently discarded. Edit in the repo, merge, then run.
setlocal
set REPO=C:\Users\george\src\homelab
set SCRIPT=%REPO%\hosts\winpc-5600x\media-pipeline\transcode.ps1
set QUEUE=C:\media-work\queue.tsv

git -C "%REPO%" pull --ff-only
if errorlevel 1 echo WARNING: git pull failed - running the last-pulled version
git -C "%REPO%" log -1 --format="running from commit %%h %%s"

if not exist "%QUEUE%" (
  echo ERROR: no queue at %QUEUE%
  echo Copy queue.example.tsv there and edit it.
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT%" -Queue "%QUEUE%" %*
endlocal
