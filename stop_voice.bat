@echo off
wmic process where "commandline like '%%tools.voice_ptt%%'" call terminate >nul 2>&1
echo voice listener stopped
