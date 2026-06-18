@echo off
title NE Simulator — MME / SGW / PGW (Port 9090)
color 0D
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║   NE Simulator — MME / SGW / PGW                           ║
echo ║   X1 Provisioning + X2 IRI Sender + X3 CC Sender          ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   Portal:  http://localhost:9090/ne_simulator.html          ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   Login:  ne_3ng1n33r_4G / N3@S!mul@t0r#4GLTE_2024        ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
echo [INFO] Set LIS IP to 127.0.0.1 (localhost) in the simulator config bar.
echo.

cd /d "%~dp0\portal"

python -m http.server 9090

if errorlevel 1 (
    echo.
    echo [ERROR] NE Simulator portal failed to start.
    echo         Check if port 9090 is already in use.
    pause
)
