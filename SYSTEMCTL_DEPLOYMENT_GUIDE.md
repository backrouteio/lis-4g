# LIS Production Deployment Guide - Systemctl Setup

## Overview
This guide shows how to deploy all 3 LIS services using systemctl for production-grade management with auto-restart, logging, and monitoring.

---

## 1. DEPLOY SERVICE FILES

### On LIS Server (10.80.20.56):
```bash
sudo cp lis-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lis-server
sudo systemctl start lis-server
sudo systemctl status lis-server
```

### On LEA Machine (10.80.20.45):
```bash
sudo cp lea-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lea-agent
sudo systemctl start lea-agent
sudo systemctl status lea-agent
```

### On NE Machine (10.80.20.62):
```bash
sudo cp ne-simulator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ne-simulator
sudo systemctl start ne-simulator
sudo systemctl status ne-simulator
```

---

## 2. VERIFY SERVICES ARE RUNNING

```bash
sudo systemctl status lis-server lea-agent ne-simulator
```

Expected output:
```
● lis-server.service - LIS Standalone Server
   Loaded: loaded (/etc/systemd/system/lis-server.service; enabled; vendor preset: enabled)
   Active: active (running) since ... 
   
● lea-agent.service - LEA SFTP Server & HI2/HI3 Receiver
   Loaded: loaded (/etc/systemd/system/lea-agent.service; enabled; vendor preset: enabled)
   Active: active (running) since ...
   
● ne-simulator.service - NE Simulator
   Loaded: loaded (/etc/systemd/system/ne-simulator.service; enabled; vendor preset: enabled)
   Active: active (running) since ...
```

---

## 3. VIEW LIVE LOGS (tail -f)

### Individual Service Logs:

```bash
# LIS Server logs
sudo tail -f /var/log/lis-server.log

# LEA Agent logs
sudo tail -f /var/log/lea-agent.log

# NE Simulator logs
sudo tail -f /var/log/ne-simulator.log
```

### Using journalctl (live systemd logs):

```bash
# Real-time LIS Server logs
sudo journalctl -u lis-server.service -f

# Real-time LEA Agent logs
sudo journalctl -u lea-agent.service -f

# Real-time NE Simulator logs
sudo journalctl -u ne-simulator.service -f

# All three services at once (opens separate log streams)
sudo journalctl -u lis-server.service -f &
sudo journalctl -u lea-agent.service -f &
sudo journalctl -u ne-simulator.service -f &
```

### View All Logs for a Service (with context):

```bash
# Last 100 lines with metadata
sudo journalctl -u lis-server.service -n 100

# Last 50 lines with timestamps
sudo journalctl -u lis-server.service --since "2 hours ago" -n 50

# Search for errors only
sudo journalctl -u lis-server.service -p err

# Follow and show priority levels
sudo journalctl -u lis-server.service -f --output short-precise
```

---

## 4. COMMON COMMANDS

### Start/Stop/Restart:
```bash
sudo systemctl start lis-server
sudo systemctl stop lis-server
sudo systemctl restart lis-server
sudo systemctl reload lis-server
```

### Enable/Disable (auto-start on boot):
```bash
sudo systemctl enable lis-server
sudo systemctl disable lis-server
```

### Check Service Status:
```bash
sudo systemctl status lis-server
sudo systemctl is-active lis-server
sudo systemctl is-enabled lis-server
```

### View Logs with Filters:
```bash
# Last 5 minutes
sudo journalctl -u lis-server.service --since "5 minutes ago"

# Last hour
sudo journalctl -u lis-server.service --since "1 hour ago"

# From yesterday
sudo journalctl -u lis-server.service --since "yesterday"

# Only warnings and errors
sudo journalctl -u lis-server.service -p warning

# Follow new logs (like tail -f)
sudo journalctl -u lis-server.service -f
```

---

## 5. MONITORING DASHBOARD

Access the enhanced monitoring portals via web browser:

- **LIS Dashboard**: http://10.80.20.56:8001/
- **LEA Portal**: http://10.80.20.45:8080/
- **NE Simulator**: http://10.80.20.62:8080/

Each portal shows:
- Live log viewer with detailed entries
- Real-time statistics (events, warrants, packets)
- Log level filtering
- Request/Response payloads
- HTTP status codes
- Error details

---

## 6. TROUBLESHOOTING

### Service won't start:
```bash
sudo journalctl -u lis-server.service -n 50
# Check for Python errors or missing dependencies
```

### Check if port is in use:
```bash
sudo netstat -tlnp | grep 8001
sudo lsof -i :8001
```

### View service configuration:
```bash
sudo systemctl show-environment
sudo systemctl cat lis-server.service
```

### Restart all services:
```bash
sudo systemctl restart lis-server lea-agent ne-simulator
```

### Check resource usage:
```bash
# Memory and CPU
ps aux | grep python3

# Open file descriptors
lsof -p $(pgrep -f "run_standalone.py")
```

---

## 7. LOG ROTATION

Logs are automatically rotated daily. To check:
```bash
ls -lh /var/log/lis-server.log*
ls -lh /var/log/lea-agent.log*
ls -lh /var/log/ne-simulator.log*
```

---

## 8. SYSTEMD FEATURES

### Auto-restart on failure:
- Max 5 restarts per 10 minutes
- 10-second delay between restarts
- Configured in service file: `RestartSec=10` and `StartLimitBurst=5`

### Graceful shutdown:
- Sends SIGTERM signal
- 30-second timeout before SIGKILL
- Configured in service file: `TimeoutStopSec=30`

### Environment variables:
- PYTHONUNBUFFERED=1 (real-time logging)
- TZ=Asia/Kolkata (India timezone)

---

## 9. QUICK REFERENCE - DEPLOY ALL AT ONCE

```bash
# Copy all service files
sudo cp lis-server.service lea-agent.service ne-simulator.service /etc/systemd/system/

# Reload systemd
sudo systemctl daemon-reload

# Enable all services (auto-start on boot)
sudo systemctl enable lis-server lea-agent ne-simulator

# Start all services
sudo systemctl start lis-server lea-agent ne-simulator

# Verify all running
sudo systemctl status lis-server lea-agent ne-simulator

# Watch all logs live
watch -n 1 'sudo journalctl -u lis-server.service -u lea-agent.service -u ne-simulator.service -n 20 --no-pager'
```

---

## 10. DAILY OPERATIONS

### Morning check:
```bash
sudo systemctl status lis-server lea-agent ne-simulator
sudo journalctl -u lis-server.service --since "today" -p warning
```

### View last 100 lines of all logs:
```bash
sudo tail -n 100 /var/log/lis-server.log /var/log/lea-agent.log /var/log/ne-simulator.log
```

### Archive logs before clearing:
```bash
sudo tar czf /root/lis-logs-backup-$(date +%Y%m%d).tar.gz /var/log/lis-*.log /var/log/lea-*.log /var/log/ne-*.log
```

---

**All services are now production-ready with auto-restart, comprehensive logging, and monitoring! 🚀**
