@echo off
cd /d "%~dp0"
python start_tunnel_task.py > tunnel_task.log 2>&1
