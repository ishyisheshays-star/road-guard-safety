@echo off
cd /d %~dp0
pip install -q -r requirements.txt
if "%PORT%"=="" set PORT=5002
python server.py
