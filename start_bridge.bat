@echo off
REM Launch wrapper for command_bridge.py.
REM Used by Task Scheduler at logon.
REM
REM IMPORTANT: do NOT redirect to bridge.log here. command_bridge.py opens
REM bridge.log via RotatingFileHandler and Windows refuses a concurrent
REM open from cmd.exe append-redirect. Redirect to bridge.startup.log
REM instead so we still capture pre-Python errors (interpreter not found,
REM syntax errors, etc.) without colliding with the Python logger.
cd /d C:\Projects\hedgeye-bot
py command_bridge.py >> bridge.startup.log 2>&1
