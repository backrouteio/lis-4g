# LIS-4G Backend API - Startup Guide

## System Overview

```
Machine 1 (LEA)      →  Machine 2 (LIS)  →  Machine 3 (NE)
10.80.20.50              10.80.20.14          10.80.10.168
Port 9443                Port 8001            Port 8002
HI1/HI2/HI3              Central              X1/X2/X3
```

---

## Quick Start (3 Machines)

### **Machine 1 (LEA) - 10.80.20.50**

Double-click: `start-lea.bat`

```
Expected Output:
LEA-4G Agent - Starting
API: 0.0.0.0:9443
FTP: 0.0.0.0:2121
```

---

### **Machine 2 (LIS) - 10.80.20.14**

Double-click: `start-lis.bat`

```
Expected Output:
LIS-4G Backend API - Starting
REST API: 0.0.0.0:8001
X2 TPKT Server listening on port 4000
X3 UDP Server listening on port 4001
```

---

### **Machine 3 (NE) - 10.80.10.168**

**For MME (Control Plane Only - X2 only):**
```
Double-click: start-ne.bat
```

**For SGW (User Plane - X2 + X3):**
```
Double-click: start-ne-sgw.bat
```

Expected Output:
```
NE-4G Simulator - Starting
X1 Polling: 10.80.20.14:8001
X2 IRI: TCP 4000
Auto-generation: ENABLED
```

---

## Test Workflow

### **Step 1: Create Warrant (via Postman)**

On any machine with Postman, send:

```
POST http://10.80.20.50:9443/hi1/warrants/activate

Body:
{
  "liid": "LI-2026-NIA-0100",
  "warrant_reference": "W-2026-98765",
  "warrant_status": "Active",
  "warrant_start_date": "2026-06-21T00:00:00Z",
  "warrant_end_date": "2026-12-21T23:59:59Z",
  "target_identifier_value": "+1234567890",
  "target_identifier_type": "MSISDN",
  "delivery_endpoint": {
    "address": "10.80.20.50",
    "port": 2121,
    "protocol": "FTP"
  },
  "hi1_parameters": {
    "sender_identifier": "LEA-MUMBAI-001",
    "receiver_identifier": "JIONET-CENTRAL",
    "transaction_id": "550e8400-e29b-41d4-a716-446655440001",
    "action_identifier": 0,
    "timestamp": "2026-06-21T10:00:00Z",
    "object_identifier": "auth-obj-001"
  }
}
```

Response: `"status": "SUCCESS"`

---

### **Step 2: Watch Logs**

**NE Console (Machine 3):**
```
X1 POLL from MME: 1 active tasks
X2 SENT: CallSetup to LIS 10.80.20.14:4000
X2 SENT: SMS to LIS 10.80.20.14:4000
```

**LIS Console (Machine 2):**
```
X2 connection from 10.80.10.168
X2 RECEIVED: CallSetup (ID:iri-abc123) for LIID=LI-2026-NIA-0100
HI2 SENT: CallSetup to LEA 10.80.20.50:9443
```

**LEA Console (Machine 1):**
```
HI2 RECEIVED: CallSetup from LIS
```

---

### **Step 3: Verify X2 Events (via Postman)**

```
GET http://10.80.20.14:8001/x2/iri/log

Response:
{
  "status": "SUCCESS",
  "events": [
    {
      "event_id": "iri-909b3bbe",
      "liid": "LI-2026-NIA-0100",
      "event_name": "CallSetup",
      "status": "Delivered",
      "timestamp": "2026-06-21T20:15:09"
    },
    ...
  ],
  "count": 5
}
```

---

### **Step 4: Test X3 (CC) - SGW Only**

Switch NE to SGW on Machine 3:
```
Double-click: start-ne-sgw.bat
```

NE logs should show:
```
X2 IRI: TCP 4000
X3 CC: UDP 4001 (enabled for SGW)
X3 CC: VoiceData for LIID=LI-2026-NIA-0100
```

Verify X3 in LIS:
```
GET http://10.80.20.14:8001/x3/cc/log

Response:
{
  "status": "SUCCESS",
  "packets": [
    {
      "packet_id": "cc-abc123",
      "liid": "LI-2026-NIA-0100",
      "packet_size": 245,
      "status": "Delivered",
      "timestamp": "2026-06-21T20:15:30"
    },
    ...
  ],
  "count": 3
}
```

---

## Manual Event Generation (Testing)

**Manually inject X2 event via Postman:**

```
POST http://10.80.10.168:8002/events/inject

Body:
{
  "liid": "LI-2026-NIA-0100",
  "event_name": "CallSetup"
}

Response:
{
  "status": "INJECTED",
  "event_id": "evt-manual-abc123"
}
```

---

## API Endpoints Summary

### **LEA (10.80.20.50:9443)**
- `POST /hi1/warrants/activate` - Create warrant
- `GET /hi2/iri/log` - View received X2 events
- `GET /cc/files/list` - View received X3 CC files

### **LIS (10.80.20.14:8001)**
- `GET /x1/tasks?ne=mme` - Get active tasks for NE
- `GET /x2/iri/log` - View X2 IRI events
- `GET /x3/cc/log` - View X3 CC packets
- `GET /health` - Service health status

### **NE (10.80.10.168:8002)**
- `GET /x1/status` - Polling status
- `GET /x2/log` - X2 events sent
- `POST /events/inject` - Manually inject X2 event
- `GET /health` - Service health status

---

## Troubleshooting

**NE can't reach LIS (X1 POLL error):**
- Verify LIS is running on Machine 2
- Check firewall allows port 8001
- Ping LIS from NE: `ping 10.80.20.14`

**X2 events not appearing:**
- Verify warrant is created and Active
- Check LIS logs for "X2 RECEIVED"
- Manually inject via `/events/inject`

**HI2 forward timeout:**
- Verify LEA is running on Machine 1
- Check LEA is listening on port 9443
- Increase timeout in run_standalone.py if needed

**Port already in use:**
- Use netstat: `netstat -ano | findstr :PORT`
- Kill process: `taskkill /PID <pid> /F`
- Or use different ports in start scripts

---

## Database Cleanup

Delete databases to start fresh:

```powershell
cd C:\LIS-4G\LIS-backend-api-only

del lea_agent.db
del lis_standalone.db
del ne_simulator.db
```

Then restart all services.

---

## GitHub Updates

Pull latest code before starting:

```powershell
cd C:\LIS-4G\LIS-backend-api-only
git pull origin backend-api-only
```

---

## Support

Check logs for errors and trace through the workflow.

All three services log to console with timestamps.
