# LEA Module Documentation

## Overview

**LEA (Law Enforcement Agency) Agent** receives intercepted content and manages delivery operations.

**File:** `lea_ftp_server.py`  
**FTP Port:** 21 (plaintext, tcpdump capturable)  
**API Port:** 8443  
**Interfaces:** HI2 (IRI reception), HI3 (CC reception via FTP)

## Architecture

### Core Responsibilities

1. **FTP Server** - Receive call content files from LIS
2. **HI2 Receiver** - Accept IRI events via HTTP
3. **CC File Management** - Store and track received files
4. **Status API** - Report delivery status

### Data Flow

```
LIS (X2 TPKT)
    ↓ IRI events
LEA HI2 HTTP receiver
    ↓ Store IRI events
DB (sqlite)

LIS (X3 UDP or FTP delivery)
    ↓ CC file transfer
LEA FTP server (port 21)
    ↓ Store in cc_received/
File system
    ↓ Query via API
/cc/files/list endpoint
```

## Interfaces

### HI2 - IRI Reception (HTTP POST)

**Endpoint:** `POST /hi2/iri`

**Request Body:**
```json
{
  "liid": "LI-2026-NIA-0042",
  "event_id": "evt-001",
  "event_type": "CallSetup",
  "timestamp": "2026-06-21T10:30:15Z",
  "calling_party": "+1234567890",
  "called_party": "+9876543210",
  "call_direction": "MO",
  "imsi": "310410123456789",
  "imei": "359072344138197",
  "cell_id": "cell-123-456",
  "additional_info": {}
}
```

**Response:**
```json
{
  "status": "RECEIVED",
  "event_id": "evt-001",
  "timestamp": "2026-06-21T10:30:20Z"
}
```

### HI3 - CC Reception (FTP)

**Protocol:** FTP (plaintext, port 21)

**Credentials:**
- Username: `lea`
- Password: `lea`
- Home directory: `cc_received/`

**File Naming Convention:**
```
{LIID}_{direction}_{timestamp}.cc

Example: LI-2026-NIA-0042_uplink_2026-06-21_103015.cc
```

**TCP Flow (tcpdump capturable):**
```
21/tcp → Control channel (plaintext commands)
6000-6100/tcp → Passive mode data transfers
```

### API Endpoints

#### Get FTP Status

`GET /ftp/server/status`

**Response:**
```json
{
  "status": "RUNNING",
  "host": "0.0.0.0",
  "port": 21,
  "username": "lea",
  "connections": 0
}
```

#### List Received CC Files

`GET /cc/files/list?liid=LI-2026-NIA-0042&limit=100`

**Response:**
```json
{
  "files": [
    {
      "file_id": "cc-001",
      "liid": "LI-2026-NIA-0042",
      "filename": "LI-2026-NIA-0042_cc_2026-06-21_10-30-15.cc",
      "file_size": 102400,
      "received_timestamp": "2026-06-21T10:30:20Z",
      "status": "Received",
      "delivery_method": "FTP",
      "checksum": "abc123..."
    }
  ],
  "total_count": 1,
  "total_size": 102400
}
```

#### Get CC File Details

`GET /cc/files/{file_id}`

#### Download CC File

`GET /cc/files/{file_id}/download`

Returns binary file content

#### Get HI3 CC Delivery Status

`GET /hi3/cc/status?liid=LI-2026-NIA-0042`

**Response:**
```json
{
  "pending_files": [],
  "delivered_files": [
    {
      "file_id": "cc-001",
      "liid": "LI-2026-NIA-0042",
      "filename": "LI-2026-NIA-0042_cc_2026-06-21_10-30-15.cc",
      "file_size": 102400,
      "received_timestamp": "2026-06-21T10:30:20Z",
      "status": "Received"
    }
  ]
}
```

## Database Schema

### ftp_files table
```sql
CREATE TABLE ftp_files (
    id INTEGER PRIMARY KEY,
    liid TEXT NOT NULL,
    filename TEXT UNIQUE NOT NULL,
    file_size INTEGER,
    received_timestamp TEXT NOT NULL,
    status TEXT,  -- 'Pending', 'Received', 'Archived'
    checksum TEXT,
    created_at TEXT
)
```

### iri_events table
```sql
CREATE TABLE iri_events (
    id INTEGER PRIMARY KEY,
    liid TEXT NOT NULL,
    event_id TEXT UNIQUE NOT NULL,
    event_type TEXT,
    timestamp TEXT NOT NULL,
    calling_party TEXT,
    called_party TEXT,
    imsi TEXT,
    imei TEXT,
    created_at TEXT
)
```

## File System Structure

```
lea_agent/
├── cc_received/           # FTP home directory
│   ├── LI-2026-NIA-0042/  # Subdirectory per LIID
│   │   ├── cc_001.cc
│   │   ├── cc_002.cc
│   └── LI-2024-IB-0001/
│       └── cc_001.cc
├── logs/
│   └── lea-agent.log
└── lea_agent.db           # SQLite database
```

## Configuration

Edit `config/lea-config.yaml`:

```yaml
ftp:
  enabled: true
  host: 0.0.0.0
  port: 21
  username: lea
  password: lea
  passive_ports: "6000-6100"
  home_directory: ./cc_received
  max_connections: 50
  idle_timeout: 300

hi2:
  enabled: true
  port: 8443
  protocol: HTTPS
```

## Starting LEA Agent

**From command line:**
```bash
python lea_ftp_server.py --host 0.0.0.0 --ftp-port 21 --api-port 8443
```

**From batch file (Windows):**
```batch
start-lea.bat
```

**As systemd service (Linux):**
```bash
systemctl start lea-agent
```

## FTP Connection Test

**Using CLI (Windows Command Prompt):**
```bash
ftp 10.80.20.45
# username: lea
# password: lea
# cd cc_received
# ls (or dir)
```

**Using Python requests:**
```python
import ftplib

ftp = ftplib.FTP('10.80.20.45')
ftp.login('lea', 'lea')
ftp.cwd('cc_received')
files = ftp.nlst()
print(files)
ftp.quit()
```

**tcpdump (plaintext visibility):**
```bash
tcpdump -i eth0 tcp port 21 -A -v
# Shows FTP commands in plaintext
```

## Logging

Logs at: `logs/lea-agent.log`

**Key log entries:**
- "FTP: User lea logged in"
- "FTP: STOR filename.cc - uploaded"
- "HI2: IRI event received - LIID=LI-2026-NIA-0042"

## Integration Points

- **LIS** → Sends IRI via X2/HI2 HTTP POST
- **LIS** → FTP connects to deliver CC files to port 21
- **NE Simulator** → Initiates FTP connection for CC delivery

## Troubleshooting

| Issue | Cause | Solution |
|-------|-------|----------|
| Cannot connect FTP port 21 | FTP server not running | python lea_ftp_server.py |
| Authentication failed | Wrong credentials | Use: lea/lea |
| Files not appearing | Wrong home directory | Check cc_received/ exists |
| HI2 API not responding | Port 8443 blocked | Check firewall |
| tcpdump shows nothing | Using SFTP instead of FTP | Verify plain FTP connection |

## Security Notes

- **FTP is plaintext** - Intentional for tcpdump testing
- **No encryption** - For development/testing only
- **Default credentials** - Change in production
- **No connection limits** - Set passive_ports range appropriately
