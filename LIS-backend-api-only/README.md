# LIS-4G Backend API Only (Production Edition)

A Python-based 4G LTE Lawful Interception System (LIS) with REST APIs for warrant management and packet delivery. This is a backend-only version with no web portals — all interaction via APIs, Postman, or CLI.

## Quick Start

### Windows Installation

1. **Install Python 3.9+**
   ```
   python --version
   ```

2. **Install dependencies**
   ```
   pip install -r requirements.txt
   ```

3. **Run all services** (use batch files)
   ```
   start-lis.bat      # LIS Server (port 8001)
   start-lea.bat      # LEA Agent (FTP port 21, API port 8443)
   start-ne.bat       # NE Simulator (X1/X2/X3 ports 4000-4001)
   ```

## Architecture

### Three Main Components

| Component | Port | Interface | Purpose |
|-----------|------|-----------|---------|
| **LIS** (run_standalone.py) | 8001 | REST API | Warrant activation, task provisioning |
| **LEA** (lea_ftp_server.py) | 21 (FTP), 8443 (API) | FTP + REST API | Call content reception, delivery |
| **NE** (ne_simulator.py) | 4000 (X2), 4001 (X3) | TPKT/UDP | Network element simulation |

### Interface Definitions

- **HI1:** Warrant activation/deactivation (REST API)
- **X1:** Task provisioning (HTTP polling)
- **X2:** IRI delivery (TPKT/RFC1006 on TCP 4000)
- **X3:** CC delivery (UDP on 4001)
- **HI2:** IRI delivery to LEA (HTTP)
- **HI3:** CC delivery to LEA (FTP)

## API Testing

### Using Postman

1. Import `postman-collection.json` into Postman
2. Select environment (LIS, LEA, NE)
3. Execute pre-configured requests

### Using CLI

```bash
# Activate warrant
curl -X POST http://localhost:8001/hi1/warrants/activate \
  -H "Content-Type: application/json" \
  -d @test-data/warrants.json

# List active warrants
curl http://localhost:8001/hi1/warrants/list

# Get X1 tasks
curl http://localhost:8001/x1/tasks?ne=mme
```

## Configuration

- **lis-config.yaml:** LIS server settings
- **lea-config.yaml:** LEA agent settings
- **ne-config.yaml:** NE simulator settings

See `config/` directory for details.

## Test Data

- **warrants.json:** Sample HI1 warrants with LIID, LEA/CSP IDs
- **events.json:** Sample IRI events for X2 delivery
- **sample-targets.json:** Target identifiers (MSISDN, IMSI, Email)

## Documentation

- **MODULE-LIS.md:** LIS architecture and HI1/X1 interface details
- **MODULE-LEA.md:** LEA agent and HI2/HI3 interface details
- **MODULE-NE-SIMULATOR.md:** NE simulator and X2/X3 delivery details
- **API-REFERENCE.md:** Complete API endpoint documentation
- **DEPLOYMENT-WINDOWS.md:** Windows installation guide

## Key Files

```
LIS-backend-api-only/
├── run_standalone.py              # LIS API server
├── lea_ftp_server.py              # LEA FTP + API server
├── ne_simulator.py                # NE Simulator
├── requirements.txt               # Python dependencies
├── README.md                       # This file
├── API-REFERENCE.md               # Full API documentation
├── DEPLOYMENT-WINDOWS.md          # Windows setup guide
├── MODULE-LIS.md                  # LIS module docs
├── MODULE-LEA.md                  # LEA module docs
├── MODULE-NE-SIMULATOR.md         # NE Simulator docs
├── config/
│   ├── lis-config.yaml            # LIS configuration
│   ├── lea-config.yaml            # LEA configuration
│   └── ne-config.yaml             # NE configuration
├── scripts/
│   ├── start-lis.bat              # Windows LIS startup
│   ├── start-lea.bat              # Windows LEA startup
│   ├── start-ne.bat               # Windows NE startup
│   └── requirements.txt           # Dependencies
├── test-data/
│   ├── warrants.json              # Sample HI1 warrants
│   ├── events.json                # Sample IRI events
│   └── sample-targets.json        # Target identifiers
├── api-docs/
│   ├── lis-openapi.yaml           # LIS Swagger spec
│   ├── lea-openapi.yaml           # LEA Swagger spec
│   └── ne-openapi.yaml            # NE Swagger spec
└── postman-collection.json        # Postman test collection
```

## HI1 Warrant Lifecycle

1. **CREATE** - LEA sends warrant activation request (POST /hi1/warrants/activate)
2. **ACTIVE** - LIS stores warrant, NE polls X1 for tasks
3. **UPDATE** - LEA modifies warrant details (PUT /hi1/warrants/update)
4. **DELETE** - LEA deactivates warrant (DELETE /hi1/warrants/delete)
5. **DELIVERY** - IRI/CC delivered to LEA via X2/X3 → HI2/HI3

## Key Parameters

### HI1 Warrant Structure
- **LIID:** Lawful Interception Identifier (e.g., LI-2026-NIA-0042)
- **WarrantReference:** Legal warrant number
- **SenderIdentifier:** LEA endpoint ID
- **ReceiverIdentifier:** CSP/Network endpoint ID
- **TargetIdentifierValue:** Phone/IMSI being intercepted
- **DeliveryEndpoint:** LEA FTP server address/port

See `test-data/warrants.json` for complete examples.

## Support

For API endpoint details, see `API-REFERENCE.md`  
For module architecture, see `MODULE-*.md` files  
For deployment on Windows, see `DEPLOYMENT-WINDOWS.md`
