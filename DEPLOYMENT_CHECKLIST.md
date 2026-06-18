# LIS Production Deployment Checklist

---

## 🖥️ **SERVER 1: LIS Server (10.80.20.56)**

### Pre-deployment
- [ ] SSH access confirmed: `ssh root@10.80.20.56`
- [ ] Verify Python 3.12+ installed: `python3 --version`
- [ ] Verify Git installed: `git --version`

### Step 1: Pull Latest Code
```bash
cd ~/lis-4g
git pull origin main
```
- [ ] Code pulled successfully
- [ ] Check for conflicts: `git status`

### Step 2: Deploy Systemctl Service
```bash
sudo cp lis-server.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lis-server
```
- [ ] Service file copied to /etc/systemd/system/
- [ ] Systemd reloaded
- [ ] Auto-start enabled: `sudo systemctl is-enabled lis-server` → `enabled`

### Step 3: Start the Service
```bash
sudo systemctl start lis-server
sleep 3
sudo systemctl status lis-server
```
- [ ] Service started successfully
- [ ] Status shows: `Active: active (running)`
- [ ] No errors in status output

### Step 4: Verify Services are Listening
```bash
sudo netstat -tlnp | grep -E "8001|4000|4001"
```
- [ ] Port 8001 listening (HTTP API)
- [ ] Port 4000 listening (X2 TCP)
- [ ] Port 4001 listening (X3 UDP)

### Step 5: Check Logs
```bash
sudo tail -f /var/log/lis-server.log
# Wait 10 seconds, then Ctrl+C
```
- [ ] Logs show "Loaded X active warrants from DB"
- [ ] Logs show "X2 TCP server 0.0.0.0:4000"
- [ ] Logs show "X3 UDP server 0.0.0.0:4001"
- [ ] No ERROR messages in logs

### Step 6: Access Dashboard
```bash
# From your laptop, open browser:
http://10.80.20.56:8001/
```
- [ ] Dashboard loads successfully
- [ ] Shows LIS Connected status
- [ ] Shows system statistics

### Step 7: Keep Service Running (Optional)
```bash
# Set to auto-restart on failure
sudo systemctl enable lis-server
```
- [ ] Service will auto-restart on reboot
- [ ] Service will auto-restart if it crashes

---

## 📊 **SERVER 2: LEA Agent (10.80.20.45)**

### Pre-deployment
- [ ] SSH access confirmed: `ssh root@10.80.20.45`
- [ ] Verify Python 3.12+ installed: `python3 --version`
- [ ] Verify Git installed: `git --version`
- [ ] SFTP directory exists: `ls -la /root/hi3_received/`

### Step 1: Pull Latest Code
```bash
cd ~/lis-4g
git pull origin main
```
- [ ] Code pulled successfully
- [ ] Check for conflicts: `git status`

### Step 2: Deploy Systemctl Service
```bash
sudo cp lea-agent.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable lea-agent
```
- [ ] Service file copied to /etc/systemd/system/
- [ ] Systemd reloaded
- [ ] Auto-start enabled: `sudo systemctl is-enabled lea-agent` → `enabled`

### Step 3: Start the Service
```bash
sudo systemctl start lea-agent
sleep 3
sudo systemctl status lea-agent
```
- [ ] Service started successfully
- [ ] Status shows: `Active: active (running)`
- [ ] No errors in status output

### Step 4: Verify Services are Listening
```bash
sudo netstat -tlnp | grep -E "2222|8443|8080"
```
- [ ] Port 2222 listening (SFTP)
- [ ] Port 8443 listening (HI2 HTTPS)
- [ ] Port 8080 listening (Portal HTTP)

### Step 5: Check Logs
```bash
sudo tail -f /var/log/lea-agent.log
# Wait 10 seconds, then Ctrl+C
```
- [ ] Logs show "SFTP server listening on 0.0.0.0:2222"
- [ ] Logs show "HI2 receiver listening on 0.0.0.0:8443"
- [ ] Logs show "Portal listening on 0.0.0.0:8080"
- [ ] No ERROR messages in logs

### Step 6: Access Portal
```bash
# From your laptop, open browser:
http://10.80.20.45:8080/
```
- [ ] Portal loads successfully
- [ ] Shows LEA Portal interface
- [ ] Shows IRI Event Log section
- [ ] Shows HI3 Files section

### Step 7: Verify SFTP Access
```bash
# From any machine with SFTP:
sftp root@10.80.20.45
# Password: (your password)
ls -la
exit
```
- [ ] SFTP login successful
- [ ] Can list files

---

## 🔧 **SERVER 3: NE Simulator (10.80.20.62)**

### Pre-deployment
- [ ] SSH access confirmed: `ssh root@10.80.20.62`
- [ ] Verify Python 3.12+ installed: `python3 --version`
- [ ] Verify Git installed: `git --version`
- [ ] Portal directory exists: `ls -la ~/lis-4g/portal/`

### Step 1: Pull Latest Code
```bash
cd ~/lis-4g
git pull origin main
```
- [ ] Code pulled successfully
- [ ] Check for conflicts: `git status`

### Step 2: Deploy Systemctl Service
```bash
sudo cp ne-simulator.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable ne-simulator
```
- [ ] Service file copied to /etc/systemd/system/
- [ ] Systemd reloaded
- [ ] Auto-start enabled: `sudo systemctl is-enabled ne-simulator` → `enabled`

### Step 3: Start the Service
```bash
sudo systemctl start ne-simulator
sleep 3
sudo systemctl status ne-simulator
```
- [ ] Service started successfully
- [ ] Status shows: `Active: active (running)`
- [ ] No errors in status output

### Step 4: Start Portal HTTP Server
```bash
cd ~/lis-4g/portal
python3 -m http.server 9090 > /tmp/http_server.log 2>&1 &
sleep 2
ps aux | grep "http.server"
```
- [ ] HTTP server started (PID shown)
- [ ] Server running on port 9090

### Step 5: Check NE Simulator Logs
```bash
sudo tail -f /var/log/ne-simulator.log
# Wait 10 seconds, then Ctrl+C
```
- [ ] Logs show "X1 poll → 10.80.20.56:8001/x1/tasks/mme"
- [ ] Logs show "X2 TCP: connected 10.80.20.56:4000"
- [ ] Logs show "X3 UDP ready → 10.80.20.56:4001"
- [ ] STATUS log shows every 15 seconds with connection info

### Step 6: Access NE Simulator Portal
```bash
# From your laptop, open browser:
http://10.80.20.62:9090/ne_simulator.html
```
- [ ] Portal loads successfully
- [ ] Shows "NE Simulator" header
- [ ] Shows event type selector buttons
- [ ] Shows detailed activity log panel (on right side)

### Step 7: Test X1 Poll
```bash
# In NE Simulator portal:
# 1. Go to "X1 Provisioning Tasks" tab
# 2. Click "↻ Refresh"
# 3. Should show warrant tasks from LIS
```
- [ ] X1 tasks load from LIS
- [ ] Shows warrant information (LIID, target, etc.)

### Step 8: Test X2 Event Send
```bash
# In NE Simulator portal:
# 1. Go to "X2 IRI Events" tab
# 2. Select event type "ATTACH"
# 3. Click "📤 Send X2 IRI Event"
# 4. Check Activity Log on the right
```
- [ ] Event shows in Activity Log as "SENT"
- [ ] Response shows "status: accepted"
- [ ] Shows response payload with "liid" and "seq"

---

## 🔍 **VERIFICATION CHECKLIST (Run on each server)**

### Quick Status Check
```bash
sudo systemctl status lis-server lea-agent ne-simulator --no-pager
```
- [ ] All 3 services show: `Active: active (running)`

### Port Verification
```bash
# LIS Server (10.80.20.56):
sudo netstat -tlnp | grep python3

# LEA Agent (10.80.20.45):
sudo netstat -tlnp | grep python3

# NE Simulator (10.80.20.62):
sudo netstat -tlnp | grep python3
```
- [ ] LIS: Shows ports 8001, 4000, 4001
- [ ] LEA: Shows ports 2222, 8443, 8080
- [ ] NE: Shows X1/X2/X3 connections

### Log Check
```bash
# On each server:
sudo tail -n 50 /var/log/lis-server.log | grep -i "error\|failed"
sudo tail -n 50 /var/log/lea-agent.log | grep -i "error\|failed"
sudo tail -n 50 /var/log/ne-simulator.log | grep -i "error\|failed"
```
- [ ] LIS: No errors in logs
- [ ] LEA: No errors in logs
- [ ] NE: No errors in logs

---

## 🧪 **END-TO-END TEST (After all 3 servers are up)**

### Test 1: Warrant Activation
```
Browser: http://10.80.20.45:8080/
1. Go to HI1 - Warrant Activation tab
2. Create new warrant with target +919876543210
3. Click Activate
```
- [ ] Warrant created with LI-ID
- [ ] Status shows "Active"
- [ ] Portal shows in X1 tasks on NE Simulator

### Test 2: Send X2 IRI Event
```
Browser: http://10.80.20.62:9090/ne_simulator.html
1. Go to X2 IRI Events tab
2. Select event type "ATTACH"
3. Click "📤 Send X2 IRI Event"
```
- [ ] Activity log shows "SENT" status (green)
- [ ] Response shows "status: accepted"

### Test 3: Verify LEA Received Event
```
Browser: http://10.80.20.45:8080/
1. Go to IRI Event Log tab
2. Should see the ATTACH event just sent
```
- [ ] Event appears with timestamp
- [ ] Shows LIID, event type, IMSI, MSISDN
- [ ] Shows payload details

### Test 4: Monitor All Logs Live
```bash
# Terminal 1 (LIS):
ssh root@10.80.20.56
sudo tail -f /var/log/lis-server.log

# Terminal 2 (LEA):
ssh root@10.80.20.45
sudo tail -f /var/log/lea-agent.log

# Terminal 3 (NE):
ssh root@10.80.20.62
sudo tail -f /var/log/ne-simulator.log

# Then send more events from portal and watch logs update in real-time
```
- [ ] All three log windows show activity
- [ ] X1 poll logs in NE
- [ ] X2 send logs in both NE and LIS
- [ ] HI2 delivery logs in LEA

---

## 📋 **FINAL CHECKLIST**

- [ ] All 3 servers have latest code (`git pull`)
- [ ] All 3 systemctl services deployed and enabled
- [ ] All 3 services running (`systemctl status`)
- [ ] All ports verified listening (`netstat -tlnp`)
- [ ] All portals accessible via browser
- [ ] All logs showing no errors (`tail -f`)
- [ ] End-to-end test passed (warrant → X2 send → LEA receive)
- [ ] Live log monitoring confirmed working

---

## ⚙️ **DAILY OPERATIONS**

### Morning Check
```bash
# On any server:
source ~/lis-4g/QUICK_COMMANDS.sh
status_all
diagnose
```

### Live Log Monitoring
```bash
# Follow all logs simultaneously:
sudo journalctl -u lis-server.service -u lea-agent.service -u ne-simulator.service -f
```

### Restart All Services
```bash
source ~/lis-4g/QUICK_COMMANDS.sh
restart_all
```

### View Errors Only
```bash
source ~/lis-4g/QUICK_COMMANDS.sh
find_errors
```

---

**🚀 READY TO DEPLOY? FOLLOW THE STEPS ABOVE FOR EACH SERVER IN ORDER!**
