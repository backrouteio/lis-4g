# API Reference - LIS-4G Backend

Complete API endpoint documentation for LIS, LEA, and NE Simulator.

## Quick Links

- **LIS API:** http://localhost:8001/docs
- **LEA API:** http://localhost:8443/docs  
- **NE API:** http://localhost:8002/docs
- **Postman Collection:** `postman-collection.json`

---

## LIS Server (Port 8001)

### HI1 - Warrant Management

#### POST /hi1/warrants/activate
**Create and activate warrant**

```bash
curl -X POST http://localhost:8001/hi1/warrants/activate \
  -H "Content-Type: application/json" \
  -d '{
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
  }'
```

**Response:**
```json
{
  "status": "SUCCESS",
  "liid": "LI-2026-NIA-0042",
  "message": "Warrant activated"
}
```

**Status Codes:**
- 201: Created
- 400: Invalid format
- 409: LIID already exists

---

#### PUT /hi1/warrants/update
**Modify existing warrant**

```bash
curl -X PUT http://localhost:8001/hi1/warrants/update \
  -H "Content-Type: application/json" \
  -d '{
    "liid": "LI-2026-NIA-0042",
    "warrant_end_date": "2027-06-21T23:59:59Z",
    "hi1_parameters": {
      "sender_identifier": "LEA-MUMBAI-001",
      "receiver_identifier": "JIONET-CENTRAL",
      "transaction_id": "550e8400-e29b-41d4-a716-446655440010",
      "action_identifier": 0,
      "timestamp": "2026-06-21T10:10:00Z",
      "object_identifier": "auth-obj-001"
    }
  }'
```

---

#### DELETE /hi1/warrants/delete?liid=LI-2026-NIA-0042
**Deactivate warrant**

```bash
curl -X DELETE "http://localhost:8001/hi1/warrants/delete?liid=LI-2026-NIA-0042"
```

**Response:**
```json
{
  "status": "SUCCESS",
  "message": "Warrant LI-2026-NIA-0042 deactivated"
}
```

---

#### GET /hi1/warrants/list?status=Active
**List warrants**

```bash
curl "http://localhost:8001/hi1/warrants/list?status=Active"
```

**Query Parameters:**
- `status`: "Active", "Inactive", "All" (default: All)

**Response:**
```json
{
  "status": "SUCCESS",
  "warrants": [
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
        "object_identifier": "auth-obj-001"
      }
    }
  ],
  "count": 1
}
```

---

### X1 - Task Provisioning

#### GET /x1/tasks?ne=mme
**Get active intercept tasks for network element**

```bash
curl "http://localhost:8001/x1/tasks?ne=mme"
```

**Query Parameters:**
- `ne`: "mme" (MME), "sgw" (SGW), "pgw" (PGW)

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

---

### X2 - IRI Delivery Logs

#### GET /x2/iri/log
**Get IRI event delivery log**

```bash
curl "http://localhost:8001/x2/iri/log"
```

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

#### DELETE /x2/iri/log/clear
**Clear IRI event log**

```bash
curl -X DELETE "http://localhost:8001/x2/iri/log/clear"
```

---

### X3 - CC Delivery Logs

#### GET /x3/cc/log
**Get CC packet delivery log**

```bash
curl "http://localhost:8001/x3/cc/log"
```

#### DELETE /x3/cc/log/clear
**Clear CC packet log**

```bash
curl -X DELETE "http://localhost:8001/x3/cc/log/clear"
```

---

### Health Check

#### GET /health
**Server health status**

```bash
curl "http://localhost:8001/health"
```

**Response:**
```json
{
  "status": "OK",
  "service": "LIS-4G Backend API",
  "timestamp": "2026-06-21T10:00:00Z",
  "interfaces": {
    "HI1": "active",
    "X1": "active",
    "X2": "active",
    "X3": "active"
  }
}
```

---

## LEA Agent (Port 8443)

### HI2 - IRI Reception

#### POST /hi2/iri
**Receive IRI event from LIS**

```bash
curl -X POST http://localhost:8443/hi2/iri \
  -H "Content-Type: application/json" \
  -d '{
    "liid": "LI-2026-NIA-0042",
    "event_id": "evt-001",
    "event_type": "CallSetup",
    "timestamp": "2026-06-21T10:30:15Z",
    "calling_party": "+1234567890",
    "called_party": "+9876543210",
    "call_direction": "MO",
    "imsi": "310410123456789"
  }'
```

---

### HI3 - CC File Management

#### GET /ftp/server/status
**Check FTP server status**

```bash
curl "http://localhost:8443/ftp/server/status"
```

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

#### GET /cc/files/list
**List received CC files**

```bash
curl "http://localhost:8443/cc/files/list?liid=LI-2026-NIA-0042&limit=100"
```

**Query Parameters:**
- `liid`: Filter by LIID (optional)
- `limit`: Max results (default: 100)

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
      "checksum": "sha256..."
    }
  ],
  "total_count": 1,
  "total_size": 102400
}
```

#### GET /cc/files/{file_id}
**Get CC file details**

```bash
curl "http://localhost:8443/cc/files/cc-001"
```

#### GET /cc/files/{file_id}/download
**Download CC file**

```bash
curl "http://localhost:8443/cc/files/cc-001/download" -o file.cc
```

---

## NE Simulator (Port 8002)

### Configuration

#### GET /config
**Get current configuration**

```bash
curl "http://localhost:8002/config"
```

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

#### PUT /config
**Update configuration**

```bash
curl -X PUT http://localhost:8002/config \
  -H "Content-Type: application/json" \
  -d '{
    "ne_type": "MME",
    "lis_ip": "10.80.20.85",
    "lis_port": 8001,
    "auto_generation_enabled": true,
    "auto_generation_interval": 10
  }'
```

---

### X1 - Task Polling Status

#### GET /x1/status
**Get X1 polling status**

```bash
curl "http://localhost:8002/x1/status"
```

---

### X2 - IRI Delivery

#### GET /x2/status
**Get X2 delivery status**

```bash
curl "http://localhost:8002/x2/status"
```

#### GET /x2/log?limit=100&liid=LI-2026-NIA-0042
**Get X2 event delivery log**

```bash
curl "http://localhost:8002/x2/log?limit=50"
```

---

### X3 - CC Delivery

#### GET /x3/status
**Get X3 delivery status**

```bash
curl "http://localhost:8002/x3/status"
```

#### GET /x3/log?limit=100
**Get X3 packet delivery log**

```bash
curl "http://localhost:8002/x3/log?limit=50"
```

---

### Event Injection

#### POST /events/inject
**Manually inject IRI event for testing**

```bash
curl -X POST http://localhost:8002/events/inject \
  -H "Content-Type: application/json" \
  -d '{
    "liid": "LI-2026-NIA-0042",
    "event_name": "CallSetup",
    "timestamp": "2026-06-21T10:30:15Z",
    "calling_party": "+1234567890",
    "called_party": "+9876543210",
    "call_direction": "MO",
    "imsi": "310410123456789",
    "imei": "359072344138197",
    "cell_id": "cell-123-456"
  }'
```

#### POST /events/cc-inject
**Manually inject CC packet for testing**

```bash
curl -X POST http://localhost:8002/events/cc-inject \
  -H "Content-Type: application/json" \
  -d '{
    "liid": "LI-2026-NIA-0042",
    "packet_data": "base64-encoded-data",
    "timestamp": "2026-06-21T10:30:15Z",
    "direction": "Uplink"
  }'
```

---

### Auto-Generation Control

#### POST /auto-generation
**Enable/disable auto-generation of events**

```bash
curl -X POST http://localhost:8002/auto-generation \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "interval": 10
  }'
```

---

## HTTP Status Codes

| Code | Meaning | Use |
|------|---------|-----|
| 200 | OK | Successful GET, PUT, DELETE |
| 201 | Created | Successful POST (warrant activated) |
| 400 | Bad Request | Invalid JSON or missing fields |
| 404 | Not Found | LIID/resource doesn't exist |
| 409 | Conflict | LIID already exists (duplicate) |
| 500 | Server Error | Internal server error |

---

## Error Response Format

All errors follow this format:

```json
{
  "status": "ERROR",
  "error_code": 404,
  "error_description": "Warrant LI-2026-NIA-0042 not found",
  "timestamp": "2026-06-21T10:00:05Z"
}
```

---

## HI1 Parameters Reference

Required fields for all HI1 operations:

| Field | Type | Example | Description |
|-------|------|---------|-------------|
| sender_identifier | string | LEA-MUMBAI-001 | LEA endpoint ID |
| receiver_identifier | string | JIONET-CENTRAL | CSP/Network ID |
| transaction_id | string UUID | 550e8400-e29b-41d4-a716-446655440001 | Unique request UUID |
| action_identifier | integer | 0 | Sequential (0, 1, 2...) |
| timestamp | ISO 8601 | 2026-06-21T10:00:00Z | Request timestamp |
| object_identifier | string | auth-obj-001 | Warrant object ID |

---

## Warrant Target Types

| Type | Example | Use Case |
|------|---------|----------|
| MSISDN | +1234567890 | Mobile phone number |
| IMSI | 310410123456789 | International Mobile Subscriber Identity |
| IMEI | 359072344138197 | International Mobile Equipment Identity |
| Email | user@example.com | Email address |

---

## Testing Workflow

1. **Activate Warrant:**
   ```bash
   POST /hi1/warrants/activate
   ```

2. **Verify Task Provisioning:**
   ```bash
   GET /x1/tasks?ne=mme
   ```

3. **Check IRI Delivery:**
   ```bash
   GET /x2/iri/log
   ```

4. **Check CC Files:**
   ```bash
   GET /cc/files/list
   ```

5. **Deactivate Warrant:**
   ```bash
   DELETE /hi1/warrants/delete?liid=LI-2026-NIA-0042
   ```

---

## See Also

- `MODULE-LIS.md` - LIS architecture and HI1/X1 details
- `MODULE-LEA.md` - LEA FTP/HI2/HI3 details
- `MODULE-NE-SIMULATOR.md` - NE X2/X3 delivery details
- `DEPLOYMENT-WINDOWS.md` - Installation and deployment guide
- `postman-collection.json` - Ready-to-use API requests
