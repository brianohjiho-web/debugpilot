@echo off
title DebugPilot share link
cd /d "%~dp0"
echo.
echo  DebugPilot share link
echo  ---------------------
echo  This makes a link anyone can open on a phone or another computer.
echo  It works while this window AND the DebugPilot window are open. Close this window to stop sharing.
echo.
if not exist "cloudflared.exe" (
  echo  First time only: downloading Cloudflare's free tunnel program, cloudflared.exe ...
  curl -L -o cloudflared.exe https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe
  if not exist "cloudflared.exe" (
    echo  The download didn't work. Check your internet and try again.
    pause
    exit /b 1
  )
)
echo  Starting... your link will appear below, in a box, ending in .trycloudflare.com
echo  Copy it and open it on any device. The scan page is the link plus /scan
echo.
cloudflared.exe tunnel --no-autoupdate --url http://localhost:8000
pause
