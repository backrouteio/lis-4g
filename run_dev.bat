@echo off
echo ============================================
echo  LIS Dev Environment Startup
echo ============================================

cd /d %~dp0

echo [1/3] Starting infrastructure (Postgres, Redis, Kafka)...
docker-compose up -d postgres redis zookeeper kafka
timeout /t 10 /nobreak >nul

echo [2/3] Starting NE Simulators...
start "NE-MME :9001" cmd /k "python -m simulator.ne_mock --ne MME --port 9001"
start "NE-SGW :9002" cmd /k "python -m simulator.ne_mock --ne SGW --port 9002"
start "NE-PGW :9003" cmd /k "python -m simulator.ne_mock --ne PGW --port 9003"
timeout /t 3 /nobreak >nul

echo [3/3] Starting LIS services...
start "ADMF :8001"   cmd /k "uvicorn admf.main:app --host 0.0.0.0 --port 8001 --reload"
start "IRI-MF :8002" cmd /k "uvicorn iri_mf.main:app --host 0.0.0.0 --port 8002 --reload"
start "CC-MF :8003"  cmd /k "uvicorn cc_mf.main:app --host 0.0.0.0 --port 8003 --reload"
timeout /t 3 /nobreak >nul

echo.
echo ============================================
echo  ALL SERVICES STARTED
echo ============================================
echo  Portal:      portal\index.html  (open in browser)
echo  ADMF API:    http://localhost:8001/docs
echo  IRI-MF API:  http://localhost:8002/docs
echo  CC-MF API:   http://localhost:8003/docs
echo  NE-MME:      http://localhost:9001/docs
echo ============================================
echo.
start "" "portal\index.html"
pause
