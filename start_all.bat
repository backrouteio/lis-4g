@echo off
title LIS Suite — Starting All Components
color 0A
echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║       LIS Suite — Starting All Three Components             ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   LIS Server    →  http://localhost:8001                    ║
echo ║   LEA Portal    →  http://localhost:8080                    ║
echo ║   NE Simulator  →  http://localhost:9090/ne_simulator.html  ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║   CREDENTIALS                                               ║
echo ║   LIS:  lis_adm!n_d0t   / L!S@Adm1n#2024$IN_D0T          ║
echo ║   LEA:  lea_0ff!c3r_1B  / L3A@0ff!c3r#IB_2024$MHA        ║
echo ║   NE:   ne_3ng1n33r_4G  / N3@S!mul@t0r#4GLTE_2024        ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
echo [INFO] Opening three terminal windows...
echo [INFO] Close this window to stop ALL services.
echo.

cd /d "%~dp0"

:: Start LIS Server in new window
start "LIS Server :8001" cmd /k "color 0B && python run_standalone.py --host 0.0.0.0 --port 8001"

:: Wait 3 seconds for LIS to start
timeout /t 3 /nobreak >nul

:: Start LEA Agent in new window
start "LEA Agent :8080/:2222/:8443" cmd /k "color 0E && python lea_sftp_server.py --sftp-port 2222 --hi2-port 8443 --portal-port 8080"

:: Wait 2 seconds
timeout /t 2 /nobreak >nul

:: Start NE Simulator in new window
start "NE Simulator :9090" cmd /k "color 0D && cd portal && python -m http.server 9090"

:: Wait for LIS to be ready then open browsers
timeout /t 4 /nobreak >nul

echo [INFO] Opening portals in browser...
start "" "http://localhost:8001"
timeout /t 1 /nobreak >nul
start "" "http://localhost:8080"
timeout /t 1 /nobreak >nul
start "" "http://localhost:9090/ne_simulator.html"

echo.
echo [OK] All components started. Portals opened in browser.
echo [INFO] Configure LIS IP as 127.0.0.1 in LEA and NE portals.
echo.
pause
