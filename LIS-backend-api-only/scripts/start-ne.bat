@echo off
REM NE Simulator - Start Network Element Simulator (Windows)
REM
REM Simulates 4G network element (MME/SGW/PGW) and auto-generates
REM IRI and CC events for testing

setlocal enabledelayedexpansion

echo ========================================================================
echo LIS-4G Backend API - Starting NE Simulator
echo ========================================================================
echo.
echo Network Element: MME (Mobility Management Entity)
echo X1 Polling: LIS at 10.80.20.85:8001 (every 5 seconds)
echo X2 Delivery: TCP port 4000 (TPKT/ASN.1 for IRI)
echo X3 Delivery: UDP port 4001 (ULIC for CC)
echo.
echo API: http://localhost:8002
echo  - Manual event injection
echo  - Configuration management
echo  - Delivery status monitoring
echo.
echo Auto-generation: ENABLED
echo  - Generates realistic IRI events
echo  - Simulates call setup, release, SMS, data
echo  - Generates CC packets every 10 seconds
echo.
echo To stop: Press Ctrl+C
echo ========================================================================
echo.

REM Change to script directory
cd /d "%~dp0.."

REM Activate Python virtual environment if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo Virtual environment activated
    echo.
)

REM Run NE simulator
python ne_simulator.py ^
    --lis-ip 10.80.20.85 ^
    --lis-port 8001 ^
    --x2-port 4000 ^
    --x3-port 4001 ^
    --ne mme ^
    --auto ^
    --poll-interval 5 ^
    --event-interval 10 ^
    --api-port 8002

if errorlevel 1 (
    echo.
    echo ERROR: NE simulator failed to start
    echo.
    echo Troubleshooting:
    echo  - Ensure Python 3.9+ is installed: python --version
    echo  - Install dependencies: pip install -r requirements.txt
    echo  - Check LIS server is running (10.80.20.85:8001)
    echo  - Verify network connectivity to LIS
    echo.
    pause
)
