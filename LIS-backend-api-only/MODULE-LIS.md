# LIS Module Documentation

## Overview

**LIS (Lawful Interception System)** is the central orchestrator for warrant management and task provisioning in the 4G LTE lawful interception system.

**File:** `run_standalone.py`  
**Port:** 8001 (REST API)  
**Interfaces:** HI1, X1, X2 (receiver), X3 (receiver)

## Architecture

### Core Responsibilities

1. **Warrant Management (HI1)** - Accept warrant activation from LEA
2. **Task Provisioning (X1)** - Provide active tasks to network elements
3. **Delivery Coordination** - Receive IRI from X2 and CC from X3, relay to LEA
4. **Database** - Store warrants, event logs, delivery status

### Data Flow

```
LEA (HI1)
    ↓ POST /hi1/warrants/activate
LIS (Database)
    ↓ Active warrants stored
NE (X1 polling)
    ↓ GET /x1/tasks
NE gets tasks with LIID + delivery endpoint
    ↓ X2 TPKT (IRI) / X3 UDP (CC) to LIS ports 4000/4001
LIS receives events
    ↓ Logs to database
LEA (HI2/HI3)
    ← X2/X3 delivery forwarded to LEA
```

## Interfaces

### HI1 - Warrant Management (REST/HTTP)

Warrant lifecycle: CREATE → UPDATE → DELETE

#### 1. CREATE - Activate Warrant

**Endpoint:** `POST /hi1/warrants/activate`

**Request Body:**
```json
{
  "liid": "LI-2026-NIA-0042",
  "warrant_reference": "W-2026-98765",
  "warrant_status": "Active",
  "warrant_start_date": "2026-06-21T00:00:00Z",
  "warrant_end_date": "2026-12-21T23:59:59Z",
  "target_identifier_value": "+1234567890",
  "target_identifier_type": "MSISDN",
  "delivery_endpoint": {
    "address": "10.80.20.45",
    "port": 21,
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

**HI1 Parameters Explained:**
- **sender_identifier:** Endpoint ID of LEA sending warrant (nationally unique)
- **receiver_identifier:** Endpoint ID of CSP/Network receiving warrant
- **transaction_id:** UUID (RFC 4122) - globally unique, prevents duplicate processing
- **action_identifier:** Sequential number (0, 1, 2...) when multiple actions in one message
- **timestamp:** ISO 8601 date-time of request
- **object_identifier:** Unique ID for this warrant object (used in UPDATE/DELETE)

**Response:**
```json
{
  "status": "SUCCESS",
  "liid": "LI-2026-NIA-0042",
  "message": "Warrant activated"
}
```

#### 2. UPDATE - Modify Warrant

**Endpoint:** `PUT /hi1/warrants/update`

**Request Body:**
```json
{
  "liid": "LI-2026-NIA-0042",
  "warrant_reference": "W-2026-98765",
  "warrant_end_date": "2027-06-21T23:59:59Z",
  "delivery_endpoint": {
    "address": "10.80.20.45",
    "port": 21,
    "protocol": "FTP"
  },
  "hi1_parameters": {
    "sender_identifier": "LEA-MUMBAI-001",
    "receiver_identifier": "JIONET-CENTRAL",
    "transaction_id": "550e8400-e29b-41d4-a716-446655440010",
    "action_identifier": 0,
    "timestamp": "2026-06-21T10:10:00Z",
    "object_identifier": "auth-obj-001"
  }
}
```

#### 3. LIST - Query Warrants

**Endpoint:** `GET /hi1/warrants/list?status=Active`

**Query Parameters:**
- `status`: "Active", "Inactive", or "All" (default: All)

**Response:**
```json
{
  "status": "SUCCESS",
  "warrants": [
    {
      "liid": "LI-2026-NIA-0042",
      "warrant_reference": "W-2026-98765",
      "warrant_status": "Active",
      "target_identifier_value": "+1234567890",
      "hi1_parameters": {
        "sender_identifier": "LEA-MUMBAI-001",
        "receiver_identifier": "JIONET-CENTRAL"
      }
    }
  ],
  "count": 1
}
```

#### 4. DELETE - Deactivate Warrant

**Endpoint:** `DELETE /hi1/warrants/delete?liid=LI-2026-NIA-0042`

**Response:**
```json
{
  "status": "SUCCESS",
  "message": "Warrant LI-2026-NIA-0042 deactivated"
}
```

### X1 - Task Provisioning (REST/HTTP)

Network elements poll LIS for active tasks to intercept

**Endpoint:** `GET /x1/tasks?ne=mme`

**Query Parameters:**
- `ne`: Network element type (mme, sgw, pgw)

**Response:**
```json
{
  "ne": "mme",
  "tasks": [
    {
      "liid": "LI-2026-NIA-0042",
      "warrant_reference": "W-2026-98765",
      "target_identifier": "+1234567890",
      "target_type": "MSISDN",
      "delivery_endpoint": {
        "address": "10.80.20.45",
        "port": 21,
        "protocol": "FTP"
      }
    }
  ],
  "poll_interval": 5
}
```

**NE Workflow:**
1. Poll X1 every N seconds (default 5s)
2. Receive task list with LIID and delivery endpoint
3. Generate IRI events for each LIID
4. Send IRI to X2 port 4000 (TPKT/ASN.1)
5. Send CC to X3 port 4001 (UDP/ULIC)

### X2 - IRI Delivery (TPKT/RFC1006 on TCP 4000)

Not directly exposed via HTTP API. Logs are available:

**Endpoint:** `GET /x2/iri/log`

**Response:**
```json
{
  "status": "SUCCESS",
  "events": [
    {
      "event_id": "evt-001",
      "liid": "LI-2026-NIA-0042",
      "event_name": "CallSetup",
      "timestamp": "2026-06-21T10:30:20Z",
      "status": "Delivered"
    }
  ],
  "count": 5
}
```

**Clear Log:**

`DELETE /x2/iri/log/clear`

### X3 - CC Delivery (UDP on 4001)

Not directly exposed via HTTP API. Logs are available:

**Endpoint:** `GET /x3/cc/log`

**Clear Log:**

`DELETE /x3/cc/log/clear`

## Database Schema

### warrants table
```sql
CREATE TABLE warrants (
    liid TEXT UNIQUE PRIMARY KEY,
    warrant_reference TEXT,
    warrant_status TEXT,  -- 'Active' or 'Inactive'
    warrant_start_date TEXT,
    warrant_end_date TEXT,
    target_identifier_value TEXT,
    target_identifier_type TEXT,  -- MSISDN, IMSI, IMEI, Email
    delivery_address TEXT,
    delivery_port INTEGER,
    delivery_protocol TEXT,  -- FTP
    sender_identifier TEXT,
    receiver_identifier TEXT,
    transaction_id TEXT,
    action_identifier INTEGER,
    object_identifier TEXT,
    created_at TEXT,
    updated_at TEXT
)
```

### iri_events table
```sql
CREATE TABLE iri_events (
    id INTEGER PRIMARY KEY,
    liid TEXT,
    event_id TEXT UNIQUE,
    event_name TEXT,
    ts REAL,  -- Unix timestamp
    calling_party TEXT,
    called_party TEXT,
    imsi TEXT,
    imei TEXT,
    cell_id TEXT,
    status TEXT,  -- 'Delivered', 'Pending', etc
    created_at TEXT
)
```

### cc_packets table
```sql
CREATE TABLE cc_packets (
    id INTEGER PRIMARY KEY,
    liid TEXT,
    packet_id TEXT UNIQUE,
    packet_size INTEGER,
    ts REAL,  -- Unix timestamp
    direction TEXT,  -- Uplink/Downlink
    status TEXT,
    created_at TEXT
)
```

## Error Handling

### HTTP Status Codes

- **201:** Warrant successfully created
- **200:** Success (GET, PUT, DELETE)
- **400:** Invalid request format
- **404:** Warrant not found (LIID doesn't exist)
- **409:** Warrant already exists (duplicate LIID)
- **500:** Server error

### Error Response Format

```json
{
  "status": "ERROR",
  "error_code": 404,
  "error_description": "Warrant LI-2026-NIA-0042 not found",
  "timestamp": "2026-06-21T10:00:05Z"
}
```

## Configuration

Edit `config/lis-config.yaml`:

```yaml
server:
  host: 0.0.0.0
  port: 8001

hi1:
  enabled: true

x1:
  enabled: true
  port: 8001
  poll_timeout: 30

x2:
  enabled: true
  port: 4000
  protocol: TPKT
  encoding: ASN1-BER

x3:
  enabled: true
  port: 4001
  protocol: UDP
  encoding: ULIC

hi3:
  ftp_host: 10.80.20.45
  ftp_port: 21
  ftp_user: lea
  ftp_password: lea
```

## Starting LIS Server

**From command line:**
```bash
python run_standalone.py --host 0.0.0.0 --port 8001 --ftp-host 10.80.20.45 --ftp-port 21
```

**From batch file (Windows):**
```batch
start-lis.bat
```

**As systemd service (Linux):**
```bash
systemctl start lis-server
```

## API Documentation

Access Swagger UI at: `http://localhost:8001/docs`

Access ReDoc at: `http://localhost:8001/redoc`

## Logging

Logs are written to: `logs/lis-server.log`

**Log levels:** DEBUG, INFO, WARNING, ERROR, CRITICAL

**Format:** `timestamp - module - level - message`

## Key Implementation Notes

1. **LIID is the primary key** - Must be unique across all warrants
2. **Soft delete** - Warrants are marked "Inactive" but kept in DB
3. **Transaction IDs** - UUID prevents duplicate processing
4. **HI1 Parameters** - All preserved for audit trail
5. **No portal** - All interaction via REST API

## Integration Points

- **NE Simulator** → Polls X1, sends X2/X3
- **LEA Agent** → Receives warrant info, gets IRI/CC from X2/X3
- **Database** → SQLite for warrant and event storage

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| 404 Warrant not found | LIID mismatch | Check X1 response for correct LIID |
| X2/X3 connection refused | FTP server not running | Start lea_ftp_server.py |
| Duplicate LIID error | Warrant already exists | DELETE and reactivate or use UPDATE |
| No tasks in X1 response | No active warrants | Check /hi1/warrants/list for Active status |
