@echo off
REM LEA Agent - Start FTP Server and API (Windows)
REM
REM Starts the LEA Agent with:
REM - FTP Server on port 21 (plaintext, tcpdump capturable)
REM - API on port 8443 for management and CC file listing

setlocal enabledelayedexpansion

echo ========================================================================
echo LIS-4G Backend API - Starting LEA Agent
echo ========================================================================
echo.
echo FTP Server: 0.0.0.0:21
echo  - Username: lea
echo  - Password: lea
echo  - Home directory: cc_received/
echo.
echo API: http://localhost:8443
echo  - HI2 IRI reception (HTTP POST)
echo  - CC file listing and download
echo.
echo For tcpdump capture (Linux):
echo  tcpdump -i eth0 tcp port 21 -A -v
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

REM Create cc_received directory if it doesn't exist
if not exist "cc_received" (
    mkdir cc_received
    echo Created cc_received directory
)

REM Run LEA server
python lea_ftp_server.py ^
    --host 0.0.0.0 ^
    --ftp-port 21 ^
    --api-port 8443

if errorlevel 1 (
    echo.
    echo ERROR: LEA agent failed to start
    echo.
    echo Troubleshooting:
    echo  - Ensure Python 3.9+ is installed: python --version
    echo  - Install dependencies: pip install -r requirements.txt
    echo  - On Windows, port 21 requires admin privileges
    echo  - Check if another FTP server is running: netstat -ano ^| findstr :21
    echo  - Try running this batch file as Administrator
    echo.
    pause
)
