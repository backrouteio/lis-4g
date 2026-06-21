@echo off
REM NE-4G Simulator Startup Script (SGW Variant)
REM Machine 3: 10.80.10.168

echo ========================================================================
echo NE-4G Simulator - Starting (SGW - User Plane)
echo Machine: 10.80.10.168
echo NE Type: SGW (Supports X2 IRI + X3 CC)
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

REM Start NE simulator as SGW
python ne_simulator.py --lis-ip 10.80.20.14 --lis-port 8001 --ne sgw --auto --api-port 8002

pause
