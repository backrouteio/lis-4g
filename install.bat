@echo off
title LIS Suite — Install
color 0A
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║       LIS Suite Installer — India 4G LTE                    ║
echo ║       HI1 / HI2 / HI3 / X1 / X2 / X3                      ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Please install Python 3.10+ from https://python.org
    echo         Make sure to check "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python found:
python --version

echo.
echo [INFO] Installing required Python packages...
echo.

pip install fastapi uvicorn psycopg2-binary paramiko pyasn1 httpx cryptography --quiet

if errorlevel 1 (
    echo [ERROR] Package install failed. Trying with --break-system-packages...
    pip install fastapi uvicorn psycopg2-binary paramiko pyasn1 httpx cryptography --break-system-packages --quiet
)

echo.
echo [OK] All packages installed.
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║  Installation complete! Now run one of:                     ║
echo ║                                                              ║
echo ║    start_lis.bat      → LIS Server  (port 8001)            ║
echo ║    start_lea.bat      → LEA Agent   (ports 2222/8443/8080) ║
echo ║    start_ne.bat       → NE Simulator (port 9090)           ║
echo ║    start_all.bat      → All three together                  ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
pause
