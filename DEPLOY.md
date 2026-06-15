# LIS Deployment Guide — India 4G LTE

## Regulatory Framework (India)

| Law | Section | Purpose |
|-----|---------|---------|
| Indian Telegraph Act, 1885 | Section 5(2) | Voice / SMS interception |
| Information Technology Act, 2000 | Section 69 | Internet / data interception |
| UAPA, 1967 | Section 39 | Terror-related intercepts |
| NIA Act, 2008 | Section 6 | National security intercepts |

**Authorised by:** Home Secretary (Central) — Ministry of Home Affairs (MHA), or State Home Secretary  
**Indian Operators:** Airtel (404-10), Jio (405-854), Vi/Vodafone-Idea (404-20), BSNL (404-07)  
**Standards:** ETSI TS 103 221, ETSI TS 103 120, 3GPP TS 33.107/33.108, DoT LI guidelines

---

## Three-Machine Architecture (v3.0)

```
┌─────────────────┐   HI1 SOAP/XML    ┌─────────────────────────────┐    X2 ASN.1 BER     ┌────────────────────┐
│  LEA Machine    │ ─────────────────▶ │  LIS Server                 │ ◀────────────────── │  NE Simulator      │
│  hi1_lea.html   │                    │  run_standalone.py :8001    │                     │  ne_simulator.html │
│  lea_sftp_server│ ◀── HI2 HTTPS ─── │  ADMF + IRI-MF + CC-MF     │ ◀── X3 CC JSON ─── │  MME / SGW / PGW   │
│  :2222 (SFTP)   │ ◀── HI3 SFTP ──── │  PostgreSQL / SQLite        │                     │                    │
│  :8443 (HI2)    │                    │  ASN.1 BER decoder          │ ──── X1 task ─────▶ │  poll /x1/tasks    │
│  :8080 (portal) │                    │  SFTP client (paramiko)     │                     │                    │
└─────────────────┘                    └─────────────────────────────┘                     └────────────────────┘
```

## Interface Summary

| Interface | Direction | Protocol | Description |
|-----------|-----------|----------|-------------|
| HI1 | LEA → LIS | SOAP/XML HTTPS | Warrant activation (ETSI TS 103 120) |
| HI2 | LIS → LEA | HTTPS POST | IRI delivery (ASN.1 BER, 3GPP TS 33.108) |
| HI3 | LIS → LEA | SFTP (port 2222) | CC content delivery (GTP-U packets) |
| X1 | LIS → NE | REST (poll) | NE provisioning (ADMF → MME/SGW/PGW) |
| X2 | NE → LIS | REST POST | IRI events (ASN.1 BER encoded) |
| X3 | NE → LIS | REST POST | CC packets (user-plane mirrored data) |

## Port Table

| Port | Protocol | Machine | Purpose |
|------|----------|---------|---------|
| 8001 | TCP | LIS Server | HI1, X1, X2, X3 (all LIS APIs) |
| 8080 | TCP | LEA, NE | Portal servers (hi1_lea.html, ne_simulator.html) |
| 8443 | TCP | LEA | HI2 IRI receiver (pushed by LIS) |
| 2222 | TCP | LEA | SFTP — HI3 CC content receiver |

---

## Machine 1: LIS Server (76.13.211.64)

### Install

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip git

git clone https://github.com/backrouteio/lis-4g.git
cd lis-4g

pip3 install fastapi uvicorn psycopg2-binary paramiko pyasn1 httpx cryptography \
    --break-system-packages --ignore-installed
```

### Run (SQLite — simple)

```bash
python3 run_standalone.py --host 0.0.0.0 --port 8001
```

### Run (PostgreSQL)

```bash
sudo ./setup_postgres.sh
python3 run_standalone.py \
  --host 0.0.0.0 --port 8001 \
  --db-url "postgresql://lis:LisSecure2024!@localhost/lisdb"
```

### Run with SFTP (HI3 delivery to LEA)

```bash
python3 run_standalone.py \
  --host 0.0.0.0 --port 8001 \
  --sftp-host <LEA-IP> --sftp-port 2222 --sftp-user lea --sftp-pass yourpassword
```

Or configure SFTP at runtime via LEA portal → Config bar.

### systemd Service

```bash
sudo tee /etc/systemd/system/lis.service <<EOF
[Unit]
Description=LIS Standalone Server — India 4G LTE
After=network.target postgresql.service

[Service]
User=root
WorkingDirectory=/root/lis-4g
ExecStart=python3 run_standalone.py --host 0.0.0.0 --port 8001 \
  --db-url postgresql://lis:LisSecure2024!@localhost/lisdb
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable lis
sudo systemctl start lis
sudo systemctl status lis
```

---

## Machine 2: LEA (Law Enforcement Agency)

### Install

```bash
pip3 install paramiko --break-system-packages
# Copy portal directory from GitHub:
git clone https://github.com/backrouteio/lis-4g.git
cd lis-4g
python3 lea_sftp_server.py
```

### What it runs

```
SFTP server   :2222  ← receives HI3 CC files from LIS
HI2 receiver  :8443  ← receives HI2 IRI events from LIS
Portal server :8080  → serves hi1_lea.html
```

### Open the LEA portal

```
http://<LEA-IP>:8080/
```

Configure in the portal:
- **LIS IP**: 76.13.211.64
- **LIS Port**: 8001
- **SFTP Host**: (LEA machine IP)
- **SFTP Port**: 2222
- **SFTP User/Pass**: lea / yourpassword

---

## Machine 3: NE Simulator

### Run portal

```bash
# Just open ne_simulator.html in a browser, or serve it:
python3 -m http.server 8080 --directory portal/
```

### Open the NE simulator

```
http://<NE-IP>:8080/ne_simulator.html
```

Configure:
- **LIS IP**: 76.13.211.64
- **LIS Port**: 8001
- **Operator**: Airtel / Jio / Vi / BSNL
- **X2 Encoding**: ASN.1 BER (recommended)

---

## X1 Routing (Intercept Type → NE)

| Intercept Type | MME (X1) | S-GW (X1) | P-GW (X1) | IRI | CC |
|----------------|----------|-----------|-----------|-----|----|
| IRI_ONLY       | ✓        | ✗         | ✗         | ✓   | ✗  |
| CC_ONLY        | ✗        | ✓         | ✓         | ✗   | ✓  |
| IRI_AND_CC     | ✓        | ✓         | ✓         | ✓   | ✓  |

## IRI Event Flow

```
LEA activates warrant via HI1 SOAP → ADMF
  ↓ X1 (to MME if IRI enabled)
  ↓ X1 (to SGW/PGW if CC enabled)

MME detects ATTACH/DETACH/TAU/HO/etc
  → ASN.1 BER encode (IRIParameters, 3GPP TS 33.108)
  → POST /x2/iri to LIS (IRI-MF)
  ← LIS decodes ASN.1, stores in DB
  → LIS pushes HI2 to LEA HTTPS endpoint
  → LEA portal displays decoded IRI events

SGW/PGW mirror user-plane packets (GTP-U)
  → POST /x3/cc to LIS (CC-MF)
  → LIS delivers via SFTP to LEA :2222
  → LEA SFTP server writes to cc_received/
  → LEA portal displays CC packet log
```

## ASN.1 IRI Encoding (3GPP TS 33.108)

```
IRIParameters ::= SEQUENCE {
  iriVersion      [0] INTEGER,
  timeStamp       [1] GeneralizedTime,
  liID            [2] UTF8String,
  sequenceNumber  [3] INTEGER,
  iriType         [4] ENUMERATED {
    attach(0), detach(1), bearerEstablish(2), bearerRelease(3),
    locationUpdate(4), sms(5), tau(6), handover(7)
  },
  targetIMSI      [5] OCTET STRING OPTIONAL,
  targetMSISDN    [6] OCTET STRING OPTIONAL,
  cellID          [7] OCTET STRING OPTIONAL,
  tai             [8] OCTET STRING OPTIONAL,
  ueIPAddress     [9] OCTET STRING OPTIONAL,
  apn             [10] UTF8String OPTIONAL,
  bearerID        [11] INTEGER OPTIONAL,
  qci             [12] INTEGER OPTIONAL
}
```

## Update Procedure

### On Windows (developer machine)

```bash
cd F:\claude\LIS
git add -A
git commit -m "Update: <description>"
git push origin main
```

### On Ubuntu LIS Server

```bash
cd /root/lis-4g
git pull origin main
sudo systemctl restart lis
```
