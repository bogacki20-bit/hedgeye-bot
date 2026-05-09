@echo off
REM One-shot watchdog. Runs every 5 min via Task Scheduler.
REM Same redirect rule as start_bridge.bat: route cmd.exe stdout/stderr to
REM bridge.watchdog.log so it doesn't fight the running bridge for bridge.log.
cd /d C:\Projects\hedgeye-bot
py bridge_watchdog.py >> bridge.watchdog.log 2>&1
