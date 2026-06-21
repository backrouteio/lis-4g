@echo off
REM NE-4G Simulator Startup Script
REM Machine 3: 10.80.10.168

echo ========================================================================
echo NE-4G Simulator - Starting
echo Machine: 10.80.10.168
echo NE Type: MME (Control Plane Only - X2 only, no X3)
echo LIS Address: 10.80.20.14:8001
echo API Port: 8002
echo Auto-generation: ENABLED
echo ========================================================================

cd /d %~dp0

REM Delete old database
if exist ne_simulator.db (
    del ne_simulator.db
    echo Deleted old database
)

REM Start NE simulator as MME
python ne_simulator.py --lis-ip 10.80.20.14 --lis-port 8001 --ne mme --auto --api-port 8002

pause
