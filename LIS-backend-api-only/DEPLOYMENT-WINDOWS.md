# Windows Deployment Guide

## System Requirements

- **OS:** Windows 7 or higher (Windows 10/11 recommended)
- **Python:** 3.9 or higher
- **RAM:** 4GB minimum
- **Disk:** 500MB free space
- **Network:** Ethernet connection

## Step 1: Install Python

### Option A: Download from Python.org

1. Go to https://www.python.org/downloads/
2. Download Python 3.11 or later
3. Run installer
4. **IMPORTANT:** Check "Add Python to PATH"
5. Click "Install Now"

### Option B: Verify Existing Python

```cmd
python --version
pip --version
```

Both should be Python 3.9+ and pip available.

## Step 2: Extract Files

1. Extract `LIS-backend-api-only.zip` to desired location
2. Example: `C:\LIS-4G`

**Folder structure:**
```
C:\LIS-4G\
├── run_standalone.py          # LIS server
├── lea_ftp_server.py          # LEA agent
├── ne_simulator.py            # NE simulator
├── requirements.txt
├── config/
│   ├── lis-config.yaml
│   ├── lea-config.yaml
│   └── ne-config.yaml
├── scripts/
│   ├── start-lis.bat
│   ├── start-lea.bat
│   └── start-ne.bat
├── test-data/
│   ├── warrants.json
│   ├── events.json
│   └── sample-targets.json
├── api-docs/
│   ├── lis-openapi.yaml
│   ├── lea-openapi.yaml
│   └── ne-openapi.yaml
└── postman-collection.json
```

## Step 3: Install Dependencies

Open Command Prompt and run:

```cmd
cd C:\LIS-4G
pip install -r requirements.txt
```

**Expected output:**
```
Successfully installed fastapi-0.104.1 uvicorn-0.24.0 pydantic-2.5.0 ...
```

If pip is not recognized, reinstall Python with "Add Python to PATH" checked.

## Step 4: Configure IPs and Ports

Edit `config/` files to match your network:

### config/lis-config.yaml
```yaml
server:
  host: 0.0.0.0
  port: 8001

hi3:
  ftp_host: 10.80.20.45      # ← Change to LEA IP
  ftp_port: 21
```

### config/ne-config.yaml
```yaml
network_element:
  type: MME

x1:
  lis_ip: 10.80.20.85         # ← Change to LIS IP
  lis_port: 8001
```

## Step 5: Check Firewall

Ensure Windows Firewall allows:

1. **Inbound:**
   - TCP 8001 (LIS API)
   - TCP 8443 (LEA API)
   - TCP 21 (LEA FTP)
   - TCP 4000 (X2 IRI)
   - UDP 4001 (X3 CC)

2. **Outbound:**
   - TCP 8001 (NE → LIS polling)

**To add firewall rule (Run as Administrator):**

```cmd
netsh advfirewall firewall add rule name="LIS-4G" dir=in action=allow protocol=tcp localport=8001,8443,21,4000 enable=yes

netsh advfirewall firewall add rule name="LIS-4G-UDP" dir=in action=allow protocol=udp localport=4001 enable=yes
```

## Step 6: Start Services

### Option A: Individual Command Prompts (Recommended for Testing)

**Window 1 - LIS Server:**
```cmd
cd C:\LIS-4G
scripts\start-lis.bat
```

**Window 2 - LEA Agent:**
```cmd
cd C:\LIS-4G
scripts\start-lea.bat
```

**Window 3 - NE Simulator:**
```cmd
cd C:\LIS-4G
scripts\start-ne.bat
```

### Option B: Single Batch File

Create `start-all.bat`:

```batch
@echo off
start cmd /k "cd /d C:\LIS-4G && scripts\start-lis.bat"
timeout /t 2
start cmd /k "cd /d C:\LIS-4G && scripts\start-lea.bat"
timeout /t 2
start cmd /k "cd /d C:\LIS-4G && scripts\start-ne.bat"
```

Run as Administrator:
```cmd
start-all.bat
```

### Option C: Windows Task Scheduler (Auto-start)

1. Open Task Scheduler
2. Create Basic Task
3. Name: "LIS-4G Services"
4. Trigger: "At startup"
5. Action: "Start a program"
   - Program: `C:\LIS-4G\scripts\start-lis.bat`
6. Repeat for LEA and NE with 10-second delays

## Step 7: Verify Services

### Check LIS Server

```cmd
curl http://localhost:8001/health
```

**Expected response:**
```json
{"status":"OK","service":"LIS-4G Backend API",...}
```

Or open browser: http://localhost:8001/docs

### Check LEA Agent

```cmd
curl http://localhost:8443/ftp/server/status
```

**Expected response:**
```json
{"status":"RUNNING","port":21,...}
```

### Check NE Simulator

```cmd
curl http://localhost:8002/x1/status
```

**Expected response:**
```json
{"lis_connection":"Connected",...}
```

## Step 8: Create First Warrant

Using Postman or curl:

```cmd
curl -X POST http://localhost:8001/hi1/warrants/activate ^
  -H "Content-Type: application/json" ^
  -d @test-data/warrants.json
```

## Step 9: Test with Postman

1. Install Postman from https://www.postman.com/downloads/
2. Import `postman-collection.json`
3. Select environment (modify IPs as needed)
4. Run pre-configured requests

**Common test requests:**
- POST `/hi1/warrants/activate` - Create warrant
- GET `/x1/tasks?ne=mme` - Get active tasks
- GET `/x2/iri/log` - View IRI events
- GET `/cc/files/list` - View received CC files

## Network Configuration

### Three-Machine Setup

**Machine 1 (LIS Server):**
- IP: 10.80.20.85
- Ports: 8001, 4000, 4001
- Services: run_standalone.py

**Machine 2 (LEA Agent):**
- IP: 10.80.20.45
- Ports: 21 (FTP), 8443
- Services: lea_ftp_server.py

**Machine 3 (NE Simulator):**
- IP: 10.80.20.62
- Ports: 8002
- Services: ne_simulator.py

**Network Requirements:**
- All machines on same subnet (10.80.20.0/24)
- Routing between machines working
- Firewall rules configured as above

## Troubleshooting

### Port Already in Use

```cmd
netstat -ano | findstr :8001
```

This shows the process using port 8001. Either:
- Stop the process: `taskkill /PID <process_id> /F`
- Change port in `config/lis-config.yaml`

### Python Not Found

Reinstall Python with "Add Python to PATH" checked:
```cmd
python --version
```

If still not found, add to PATH:
1. Right-click This PC → Properties
2. Advanced system settings
3. Environment Variables
4. Add `C:\Users\<username>\AppData\Local\Programs\Python\Python311`

### FTP Port 21 Permission Denied (Windows)

FTP port 21 requires administrator privileges. Run batch file as Administrator:
1. Right-click `start-lea.bat`
2. Click "Run as administrator"

Or modify `config/lea-config.yaml` to use port 2121:
```yaml
ftp:
  port: 2121
```

### Cannot Connect to LIS from NE

1. Check LIS is running: `curl http://localhost:8001/health`
2. Check firewall allows outbound TCP 8001
3. Verify IP in `config/ne-config.yaml`
4. Test network: `ping 10.80.20.85`

### No IRI Events in X2 Log

1. Verify warrant is active: `curl http://localhost:8001/hi1/warrants/list`
2. Check X1 returns tasks: `curl http://localhost:8001/x1/tasks?ne=mme`
3. Verify NE auto-generation: `curl http://localhost:8002/auto-generation`
4. Check NE logs for errors

## Database Files

After first run, these files are created:

- `lis_standalone.db` - LIS warrant and event database
- `lea_agent.db` - LEA IRI event database
- `ne_simulator.db` - NE event simulation database

**To reset databases:**
```cmd
del lis_standalone.db
del lea_agent.db
del ne_simulator.db
```

Then restart services.

## Logging

Logs are written to `logs/` directory:

```
logs/
├── lis-server.log      # LIS server activity
├── lea-agent.log       # LEA FTP and API
└── ne-simulator.log    # NE polling and events
```

View logs (Windows):
```cmd
type logs\lis-server.log
# Or use tail equivalent:
powershell -Command "Get-Content logs\lis-server.log -Tail 50 -Wait"
```

## Performance Tuning

### For Low-Power Machines

Edit `config/ne-config.yaml`:
```yaml
simulation:
  event_interval: 30         # Generate events every 30 seconds
```

### For High-Load Testing

```yaml
simulation:
  event_interval: 1          # Generate events every 1 second
  auto_generate: true
```

## Security Considerations

⚠️ **WARNING:** This is a backend API only - NO authentication by default.

**For production:**
1. Add API key authentication
2. Use HTTPS instead of HTTP
3. Enable firewall rules
4. Use strong FTP credentials
5. Regular database backups
6. Log monitoring and alerts

## Next Steps

1. Read `API-REFERENCE.md` for complete endpoint documentation
2. Read `MODULE-*.md` for architecture details
3. Import `postman-collection.json` for API testing
4. Customize `config/` files for your network
5. Review `test-data/` JSON files for your own test cases

## Support

For issues:
1. Check logs in `logs/` directory
2. Review `MODULE-*.md` documentation
3. Verify network connectivity with `ping`
4. Test endpoints with Postman or curl
5. Check GitHub: https://github.com/backrouteio/lis-4g
