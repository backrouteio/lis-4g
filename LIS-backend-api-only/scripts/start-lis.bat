@echo off
REM LIS-4G Backend API - Start LIS Server (Windows)
REM
REM Starts the Lawful Interception System on port 8001
REM Interfaces: HI1 (Warrant), X1 (Tasks), X2 (IRI), X3 (CC)

setlocal enabledelayedexpansion

echo ========================================================================
echo LIS-4G Backend API - Starting LIS Server
echo ========================================================================
echo.
echo Listening on: 0.0.0.0:8001
echo HI1 Interface: Warrant management (REST API)
echo X1 Interface: Task provisioning (HTTP polling)
echo X2 Interface: IRI delivery (TCP 4000)
echo X3 Interface: CC delivery (UDP 4001)
echo.
echo Access Swagger UI at: http://localhost:8001/docs
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

REM Run LIS server
python run_standalone.py ^
    --host 0.0.0.0 ^
    --port 8001 ^
    --ftp-host 10.80.20.45 ^
    --ftp-port 21

if errorlevel 1 (
    echo.
    echo ERROR: LIS server failed to start
    echo.
    echo Troubleshooting:
    echo  - Ensure Python 3.9+ is installed: python --version
    echo  - Install dependencies: pip install -r requirements.txt
    echo  - Check port 8001 is not in use: netstat -ano ^| findstr :8001
    echo.
    pause
)
