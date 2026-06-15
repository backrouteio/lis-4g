@echo off
title LEA Agent — HI2/HI3/Portal (Ports 2222, 8443, 8080)
color 0E
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║   LEA Agent — Law Enforcement Agency Receiver               ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   SFTP (HI3):   port 2222  ← CC files from LIS            ║
echo ║   HI2 Receiver: port 8443  ← IRI events from LIS          ║
echo ║   LEA Portal:   port 8080  → http://localhost:8080         ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   Login:  lea_0ff!c3r_1B / L3A@0ff!c3r#IB_2024$MHA        ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

python lea_sftp_server.py --sftp-port 2222 --hi2-port 8443 --portal-port 8080

if errorlevel 1 (
    echo.
    echo [ERROR] LEA Agent failed to start.
    echo         Check if ports 2222, 8443, or 8080 are already in use.
    echo         Run: netstat -ano ^| findstr "2222\|8443\|8080"
    pause
)
