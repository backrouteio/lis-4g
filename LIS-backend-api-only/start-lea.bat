@echo off
REM LEA-4G Agent Startup Script
REM Machine 1: 10.80.20.50

echo ========================================================================
echo LEA-4G Agent - Starting
echo Machine: 10.80.20.50
echo API Port: 9443
echo FTP Port: 2121
echo ========================================================================

cd /d %~dp0

REM Delete old database
if exist lea_agent.db (
    del lea_agent.db
    echo Deleted old database
)

REM Start LEA server
python lea_ftp_server.py --host 0.0.0.0 --api-port 9443 --ftp-port 2121

pause
