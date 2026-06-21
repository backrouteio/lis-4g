@echo off
REM LIS-4G Backend API Startup Script
REM Machine 2: 10.80.20.14

echo ========================================================================
echo LIS-4G Backend API - Starting
echo Machine: 10.80.20.14
echo REST API Port: 8001
echo X2 TPKT Port: 4000
echo X3 UDP Port: 4001
echo LEA Address: 10.80.20.50:9443
echo ========================================================================

cd /d %~dp0

REM Git pull latest
echo.
echo Pulling latest code from GitHub...
git pull origin backend-api-only

REM Delete old database
if exist lis_standalone.db (
    del lis_standalone.db
    echo Deleted old database
)

REM Start LIS server
python run_standalone.py --host 0.0.0.0 --port 8001 --lea-host 10.80.20.50 --lea-port 9443

pause
