# NE Simulator Module Documentation

## Overview

**NE (Network Element) Simulator** generates IRI/CC events and delivers them to LIS following the X1/X2/X3 workflow.

**File:** `ne_simulator.py`  
**API Port:** 8002  
**X1 Poll Port:** 8001 (to LIS)  
**X2 Delivery Port:** 4000 (TCP TPKT)  
**X3 Delivery Port:** 4001 (UDP)  
**Types:** MME, SGW, PGW

## Architecture

### Core Responsibilities

1. **X1 Polling** - Poll LIS for active intercept tasks
2. **IRI Generation** - Create IRI events matching LIID patterns
3. **X2 Delivery** - Send IRI events via TPKT/ASN.1 to LIS port 4000
4. **X3 Delivery** - Send CC packets via UDP to LIS port 4001
5. **Auto-generation** - Continuously generate test events

### Data Flow

```
NE Simulator (X1 polling loop)
    ↓ GET /x1/tasks every 5 seconds
LIS Server
    ← Returns: LIID + task info
NE stores active LIIDs
    ↓ For each active LIID:
IRI Generator (CallSetup, Release, SMS, etc)
    ↓ X2 TPKT delivery (port 4000)
LIS X2 receiver
    ↓ Logs to DB
CC Generator (voice packets)
    ↓ X3 UDP delivery (port 4001)
LIS X3 receiver
    ← Logs to DB
```

## Interfaces

### X1 - Task Polling (HTTP GET)

NE polls LIS to get active intercept tasks

**Polling Flow:**
```bash
GET http://10.80.20.85:8001/x1/tasks?ne=mme
Response: {
  "tasks": [
    {
      "liid": "LI-2026-NIA-0042",
      "target_identifier": "+1234567890",
      "delivery_endpoint": {...}
    }
  ]
}
```

**NE Implementation:**
- Poll interval: 5 seconds (configurable)
- Store received LIIDs in memory
- Immediately generate events for new LIIDs
- Stop generating when LIID removed from response

### X2 - IRI Delivery (TPKT/RFC1006 on TCP 4000)

**Protocol Stack:**
```
TCP 4000
    ↓ TPKT header (RFC 1006)
    ↓ ASN.1 BER encoding
    ↓ IRI event structure
```

**TPKT Packet Format:**
```
Byte 0:    Version (3)
Byte 1:    Reserved (0)
Bytes 2-3: Length (big-endian)
Bytes 4+:  ASN.1 BER encoded data
```

**Manual IRI Event Injection**

`POST /events/inject`

**Request:**
```json
{
  "liid": "LI-2026-NIA-0042",
  "event_name": "CallSetup",
  "timestamp": "2026-06-21T10:30:15Z",
  "calling_party": "+1234567890",
  "called_party": "+9876543210",
  "call_direction": "MO",
  "imsi": "310410123456789",
  "imei": "359072344138197",
  "cell_id": "cell-123-456"
}
```

**Response:**
```json
{
  "status": "INJECTED",
  "event_id": "evt-manual-001"
}
```

### X3 - CC Delivery (UDP on 4001)

**Protocol:**
```
UDP 4001
    ↓ ULIC format (Unique Lawful Interception Identifier)
    ↓ Voice/data packet payload
```

**Manual CC Packet Injection**

`POST /events/cc-inject`

**Request:**
```json
{
  "liid": "LI-2026-NIA-0042",
  "packet_data": "base64-encoded-packet",
  "timestamp": "2026-06-21T10:30:15Z",
  "direction": "Uplink"
}
```

## Configuration

Edit `config/ne-config.yaml`:

```yaml
network_element:
  type: MME                 # MME, SGW, or PGW
  node_id: "MME-001"

x1:
  lis_ip: 10.80.20.85
  lis_port: 8001
  poll_interval: 5          # seconds
  protocol: HTTP

x2:
  port: 4000
  protocol: TPKT
  encoding: ASN1-BER

x3:
  port: 4001
  protocol: UDP
  encoding: ULIC

simulation:
  auto_generate: true
  event_interval: 10        # seconds between auto-generated events
  event_types:
    - IRI
    - CC
  liid_patterns:
    - "LI-2026-NIA-*"
    - "LI-2024-IB-*"
```

## API Endpoints

### Get Configuration

`GET /config`

**Response:**
```json
{
  "ne_type": "MME",
  "lis_ip": "10.80.20.85",
  "lis_port": 8001,
  "x1_poll_interval": 5,
  "x2_port": 4000,
  "x3_port": 4001,
  "auto_generation_enabled": true,
  "auto_generation_interval": 10
}
```

### Update Configuration

`PUT /config`

### Get X1 Polling Status

`GET /x1/status`

**Response:**
```json
{
  "lis_connection": "Connected",
  "last_poll": "2026-06-21T10:30:45Z",
  "active_tasks": 2,
  "poll_interval": 5
}
```

### Get X2 Delivery Status

`GET /x2/status`

**Response:**
```json
{
  "port": 4000,
  "protocol": "TPKT",
  "encoding": "ASN1-BER",
  "events_sent": 15,
  "last_event_time": "2026-06-21T10:30:50Z"
}
```

### Get X2 Event Log

`GET /x2/log?limit=100&liid=LI-2026-NIA-0042`

### Get X3 Delivery Status

`GET /x3/status`

### Get X3 Packet Log

`GET /x3/log?limit=100`

### Toggle Auto-Generation

`POST /auto-generation`

**Request:**
```json
{
  "enabled": true,
  "interval": 10
}
```

## Event Patterns

### Auto-Generated IRI Events

When auto_generation enabled, NE generates random events:

**Event Types:**
- CallSetup - Mobile initiated call
- CallRelease - Call end, includes duration
- SMS - Short message delivery
- DataConnection - GPRS/LTE activation
- LocationUpdate - Cell/LAC registration

**Event Sequence (Realistic):**
```
1. LocationUpdate (target enters network)
2. CallSetup (calling party)
3. CallRelease (call ends after 5-10 min)
4. SMS (random SMS event)
5. DataConnection (data session)
```

### Auto-Generated CC Packets

Simulated voice/data packets with:
- Random size (100-5000 bytes)
- Direction (Uplink/Downlink)
- LIID matching active tasks

## Database Schema

### simulated_events table
```sql
CREATE TABLE simulated_events (
    id INTEGER PRIMARY KEY,
    liid TEXT NOT NULL,
    event_id TEXT UNIQUE NOT NULL,
    event_name TEXT,
    ts REAL NOT NULL,
    direction TEXT,
    status TEXT,  -- 'Sent', 'Delivered', 'Failed'
    created_at TEXT
)
```

## Starting NE Simulator

**From command line:**
```bash
python ne_simulator.py --lis-ip 10.80.20.85 --lis-port 8001 \
  --x2-port 4000 --x3-port 4001 --ne mme --auto
```

**From batch file (Windows):**
```batch
start-ne.bat
```

**As systemd service (Linux):**
```bash
systemctl start ne-simulator
```

## Command-Line Arguments

```
--lis-ip            LIS server IP (default: 10.80.20.85)
--lis-port          LIS X1 polling port (default: 8001)
--x2-port           X2 delivery port (default: 4000)
--x3-port           X3 delivery port (default: 4001)
--ne                NE type: mme, sgw, pgw (default: mme)
--auto              Enable auto-generation (flag)
--poll-interval     X1 poll interval seconds (default: 5)
--event-interval    Auto-gen event interval seconds (default: 10)
--api-port          NE Simulator API port (default: 8002)
```

## Logging

Logs at: `logs/ne-simulator.log`

**Key log entries:**
- "X1 POLL: 2 active tasks"
- "X2 SEND: TPKT packet size=256 LIID=LI-2026-NIA-0042"
- "X3 SEND: UDP packet to port 4001"
- "Auto-gen: CallSetup event for LIID=LI-2026-NIA-0042"

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| X1 connection refused | LIS not running | Start run_standalone.py |
| 0 active tasks in X1 | No warrants activated | POST /hi1/warrants/activate on LIS |
| X2 connection refused | LIS not listening 4000 | Check LIS is running |
| Events not generated | auto_generation disabled | POST /auto-generation with enabled=true |
| LIID mismatch error | NE generating wrong LIID | Update ne-config.yaml or use /config PUT |

## Integration Testing

**Full end-to-end test:**

1. Start LIS: `python run_standalone.py`
2. Start LEA: `python lea_ftp_server.py`
3. Start NE: `python ne_simulator.py --auto`
4. Activate warrant: `POST /hi1/warrants/activate` on LIS
5. Check X1 tasks: `GET /x1/tasks?ne=mme` on LIS (should show task)
6. Wait 10 seconds for auto-generation
7. Check X2 log: `GET /x2/log` on LIS (should show IRI events)
8. Check X3 log: `GET /x3/log` on LIS (should show CC packets)
9. Check CC files on LEA: `GET /cc/files/list`

## Performance Notes

- **X1 polling:** ~100ms per request
- **X2 TPKT delivery:** ~1ms per event
- **X3 UDP delivery:** ~0.5ms per packet
- **Auto-generation:** Can handle 100+ events/second
- **Memory usage:** ~50MB for 1000 active LIIDs
