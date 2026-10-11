@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python make_call.py 918200344429 7e40a853 > call_run.log 2>&1
