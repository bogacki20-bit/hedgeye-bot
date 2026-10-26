@echo off
rem Hold RIGHT CTRL to talk. Runs hidden (no window); stop it with stop_voice.bat
cd /d %~dp0
start "" pyw -m tools.voice_ptt %*
