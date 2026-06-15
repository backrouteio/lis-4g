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
**Standards:** ETSI TS 103 221, 3GPP TS 33.107/33.108, DoT LI guidelines

## Architecture

```
LEA Machine          LIS Server (76.13.211.64)    NE Simulator Machine
─────────────        ──────────────────────────   ───────────────────────
hi1_lea.html  ──HI1→ run_standalone.py :8001  ←X2/X3── ne_simulator.html
              ←HI2──  (ADMF + IRI-MF + CC-MF)
              ←HI3──  PostgreSQL / SQLite
```

## Ports to Open (Firewall)

| Port | Protocol | From       | To         | Purpose              |
|------|----------|------------|------------|----------------------|
| 8001 | TCP      | LEA, NE    | LIS Server | HI1, X1, X2, X3     |
| 8443 | TCP      | LIS Server | LEA        | HI2/HI3 delivery    |

> In standalone mode, all LIS functions (HI1, X2, X3) run on **port 8001 only**.

## Quick Start — Local (Windows)

```bash
cd F:\claude\LIS
pip install fastapi uvicorn
python run_standalone.py
# Open: http://localhost:8001
```

## Ubuntu Server Deployment

### Step 1 — Install Python & dependencies

```bash
sudo apt-get update
sudo apt-get install -y python3 python3-pip git

git clone https://github.com/backrouteio/lis-4g.git
cd lis-4g

pip3 install fastapi uvicorn psycopg2-binary --break-system-packages --ignore-installed
```

### Step 2 — Set up PostgreSQL (optional but recommended)

```bash
chmod +x setup_postgres.sh
sudo ./setup_postgres.sh
```

Edit `setup_postgres.sh` first to change `DB_PASS` to something secure.

### Step 3 — Run LIS

**With SQLite (simpler, no DB setup needed):**
```bash
python3 run_standalone.py --host 0.0.0.0 --port 8001
```

**With PostgreSQL (persistent, survives restarts):**
```bash
python3 run_standalone.py \
  --host 0.0.0.0 \
  --port 8001 \
  --db-url "postgresql://lis:LisSecure2024!@localhost/lisdb"
```

### Step 4 — Run as a systemd service (auto-start on reboot)

```bash
sudo tee /etc/systemd/system/lis.service <<EOF
[Unit]
Description=LIS Standalone Server
After=network.target postgresql.service

[Service]
User=root
WorkingDirectory=/root/lis-4g
ExecStart=python3 run_standalone.py --host 0.0.0.0 --port 8001 --db-url postgresql://lis:LisSecure2024!@localhost/lisdb
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

## Portal Configuration (Multi-Machine)

### LEA Machine — hi1_lea.html
Click ⚙️ Config and set:
- **LIS IP**: 76.13.211.64
- **LIS Port**: 8001
- **LEA IP**: (LEA machine IP)
- **LEA Port**: 8443

### NE Simulator Machine — ne_simulator.html
Click ⚙️ Config and set:
- **LIS IP**: 76.13.211.64
- **LIS Port**: 8001
- **NE (local) IP**: (NE machine IP)

## IRI Event Flow (X1/X2 in Real 4G)

```
HI1: LEA activates warrant → ADMF
       ↓ X1 (to MME if IRI_ONLY or IRI_AND_CC)
       ↓ X1 (to SGW/PGW if CC_ONLY or IRI_AND_CC)
MME generates X2 IRI events → IRI-MF → HI2 → LEA
SGW/PGW mirror packets via X3 → CC-MF → HI3 → LEA
```

## Intercept Types & X1 Routing

| Intercept Type | MME (X1) | S-GW (X1) | P-GW (X1) |
|----------------|----------|-----------|-----------|
| IRI_ONLY       | ✓        | ✗         | ✗         |
| CC_ONLY        | ✗        | ✓         | ✓         |
| IRI_AND_CC     | ✓        | ✓         | ✓         |
