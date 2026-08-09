@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
call .venv\Scripts\activate.bat
echo ---- started %date% %time% ---- >> logs\pipeline.log
python pipeline.py >> logs\pipeline.log 2>&1
