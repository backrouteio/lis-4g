@echo off
title LIS Server — India 4G LTE (Port 8001)
color 0B
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║   LIS Server v3.1 — Lawful Interception System             ║
echo ║   ADMF + IRI-MF + CC-MF + HI1/HI2/HI3 + X1/X2/X3        ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   Portal:    http://localhost:8001                          ║
echo ║   API Docs:  http://localhost:8001/docs                     ║
echo ║   Login:     lis_adm!n_d0t / L!S@Adm1n#2024$IN_D0T        ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

cd /d "%~dp0"

python run_standalone.py --host 0.0.0.0 --port 8001

if errorlevel 1 (
    echo.
    echo [ERROR] LIS Server failed to start. Check if port 8001 is already in use.
    echo         Run: netstat -ano ^| findstr :8001
    pause
)
