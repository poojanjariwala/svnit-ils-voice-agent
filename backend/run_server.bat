@echo off
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
python -m uvicorn main:app --host 0.0.0.0 --port 8000 > server.log 2>&1
