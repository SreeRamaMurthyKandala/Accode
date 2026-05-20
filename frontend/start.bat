@echo off
REM Start the Accode web frontend. Run from anywhere; paths resolve to the repo.
cd /d "%~dp0.."
python frontend\server.py --config config.yaml --cwd . %*
