"""
LIS Standalone Server — Zero Docker, Zero Kafka, Zero Redis
All components in one process using SQLite + in-memory state.

Usage:
    pip install fastapi uvicorn
    python run_standalone.py

Then open: portal/index.html in your browser
"""
import sqlite3
import threading
import uuid
import logging
import argparse
from datetime import datetime
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("LIS")

# ═══════════════════════════════════════════════════════════════
#  SHARED IN-MEMORY STATE
# ═══════════════════════════════════════════════════════════════

_lock = threading.Lock()

# LIID → warrant dict
_warrants: dict[str, dict] = {}

# X1 tasks received by each mock NE
_ne_tasks: dict[str, list] = {"MME": [], "SGW": [], "PGW": []}

# IRI events received via X2
_iri_events: list[dict] = []

# CC packets received via X3
_cc_events: list[dict] = []

# Sequence counters per LIID
_seq: dict[str, int] = {}

DB_PATH = "lis_standalone.db"


# ═══════════════════════════════════════════════════════════════
#  SQLITE — Warrant persistence
# ═══════════════════════════════════════════════════════════════

def db_init():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS warrants (
            liid             TEXT PRIMARY KEY,
            lea_id           TEXT,
            target_id_type   TEXT,
            target_value     TEXT,
            intercept_type   TEXT,
            delivery_address TEXT,
            valid_from       TEXT,
            valid_until      TEXT,
            active           INTEGER DEFAULT 1,
            created_at       TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS iri_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            liid         TEXT,
            seq          INTEGER,
            event_type   TEXT,
            timestamp    TEXT,
            payload_json TEXT,
            created_at   TEXT
        )
    """)
    con.commit()
    con.close()
    logger.info("SQLite DB ready: %s", DB_PATH)


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ═══════════════════════════════════════════════════════════════
#  MAIN APP  (ADMF HI1 + X2 IRI receiver + X3 CC receiver)
# ═══════════════════════════════════════════════════════════════

app = FastAPI(title="LIS Standalone", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Models ────────────────────────────────────────────────────

class TargetIn(BaseModel):
    id_type: str
    value: str

class ActivateReq(BaseModel):
    liid: str
    lea_id: str
    target: TargetIn
    intercept_type: str = "IRI_AND_CC"
    valid_from: str
    valid_until: str
    delivery_address: str = "127.0.0.1:8443"

class DeactivateReq(BaseModel):
    liid: str
    lea_id: str

class X2Event(BaseModel):
    liid: str
    sequence_number: Optional[int] = None
    event_type: str
    timestamp: Optional[str] = None
    imsi: Optional[str] = None
    msisdn: Optional[str] = None
    imei: Optional[str] = None
    cell_id: Optional[str] = None
    tai: Optional[str] = None
    apn: Optional[str] = None
    ue_ip: Optional[str] = None

class X3Packet(BaseModel):
    liid: str
    sequence_number: Optional[int] = None
    timestamp: Optional[str] = None
    direction: str = "UPLINK"
    payload_hex: Optional[str] = None
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    protocol: Optional[int] = None


# ── HI1 — Warrant Management ─────────────────────────────────

def _x1_provision(warrant: dict, action: str):
    """Push X1 task to relevant mock NEs based on intercept type."""
    itype = warrant["intercept_type"]
    ne_targets = {
        "IRI_ONLY":   ["MME"],
        "CC_ONLY":    ["SGW", "PGW"],
        "IRI_AND_CC": ["MME", "SGW", "PGW"],
    }.get(itype, ["MME", "SGW", "PGW"])

    for ne in ne_targets:
        task = {
            "task_id":       str(uuid.uuid4()),
            "liid":          warrant["liid"],
            "target_id_type":warrant["target_id_type"],
            "target_value":  warrant["target_value"],
            "intercept_type":warrant["intercept_type"],
            "action":        action,
            "ne_name":       ne,
            "received_at":   datetime.utcnow().isoformat(),
        }
        with _lock:
            _ne_tasks[ne].append(task)
        logger.info("X1 [%s] %s → %s target=%s", ne, action, warrant["liid"], warrant["target_value"])


@app.post("/hi1/warrants/activate", tags=["HI1"])
def activate_warrant(req: ActivateReq):
    with db() as con:
        existing = con.execute("SELECT liid FROM warrants WHERE liid=?", (req.liid,)).fetchone()
        if existing:
            raise HTTPException(409, f"LIID {req.liid} already exists")
        now = datetime.utcnow().isoformat()
        con.execute("""
            INSERT INTO warrants VALUES (?,?,?,?,?,?,?,?,1,?)
        """, (req.liid, req.lea_id, req.target.id_type, req.target.value,
              req.intercept_type, req.delivery_address,
              req.valid_from, req.valid_until, now))

    warrant = {
        "liid": req.liid, "lea_id": req.lea_id,
        "target_id_type": req.target.id_type,
        "target_value": req.target.value,
        "intercept_type": req.intercept_type,
        "delivery_address": req.delivery_address,
        "valid_from": req.valid_from, "valid_until": req.valid_until,
        "active": True,
    }
    with _lock:
        _warrants[req.liid] = warrant

    _x1_provision(warrant, "ACTIVATE")
    logger.info("HI1 ACTIVATE: LIID=%s target=%s(%s)", req.liid, req.target.value, req.target.id_type)
    return {"liid": req.liid, "status": "ACTIVATED", "message": "Intercept activated successfully"}


@app.post("/hi1/warrants/deactivate", tags=["HI1"])
def deactivate_warrant(req: DeactivateReq):
    with db() as con:
        row = con.execute("SELECT * FROM warrants WHERE liid=?", (req.liid,)).fetchone()
        if not row:
            raise HTTPException(404, f"Warrant {req.liid} not found")
        if row["lea_id"] != req.lea_id:
            raise HTTPException(403, "LEA ID mismatch")
        con.execute("UPDATE warrants SET active=0 WHERE liid=?", (req.liid,))

    with _lock:
        if req.liid in _warrants:
            _warrants[req.liid]["active"] = False
        warrant = dict(row)

    _x1_provision(warrant, "DEACTIVATE")
    logger.info("HI1 DEACTIVATE: LIID=%s", req.liid)
    return {"liid": req.liid, "status": "DEACTIVATED", "message": "Intercept deactivated"}


@app.get("/hi1/warrants", tags=["HI1"])
def list_warrants():
    with db() as con:
        rows = con.execute(
            "SELECT * FROM warrants WHERE active=1 AND valid_until > ? ORDER BY created_at DESC",
            (datetime.utcnow().isoformat(),)
        ).fetchall()
    return [
        {
            "liid":           r["liid"],
            "lea_id":         r["lea_id"],
            "target_value":   r["target_value"],
            "intercept_type": r["intercept_type"],
            "valid_until":    r["valid_until"],
            "active":         bool(r["active"]),
        }
        for r in rows
    ]


@app.get("/hi1/warrants/{liid}", tags=["HI1"])
def get_warrant(liid: str):
    with db() as con:
        row = con.execute("SELECT * FROM warrants WHERE liid=?", (liid,)).fetchone()
    if not row:
        raise HTTPException(404, f"Warrant {liid} not found")
    return dict(row)


# ── X2 — IRI Events from NE ──────────────────────────────────

@app.post("/x2/iri", tags=["X2"])
def receive_iri(event: X2Event):
    with _lock:
        active = _warrants.get(event.liid, {}).get("active", False)
    if not active:
        # Also check DB for warrants added before this process started
        with db() as con:
            row = con.execute("SELECT active FROM warrants WHERE liid=?", (event.liid,)).fetchone()
        active = row and bool(row["active"])
    if not active:
        logger.warning("X2: unknown/inactive LIID=%s — discarding", event.liid)
        raise HTTPException(400, f"LIID {event.liid} is not active")

    _seq[event.liid] = _seq.get(event.liid, 0) + 1
    seq = _seq[event.liid]

    record = event.dict()
    record["sequence_number"] = seq
    record["timestamp"] = record.get("timestamp") or datetime.utcnow().isoformat()
    record["received_at"] = datetime.utcnow().isoformat()

    with _lock:
        _iri_events.append(record)

    # Persist to DB
    import json
    with db() as con:
        con.execute(
            "INSERT INTO iri_log (liid,seq,event_type,timestamp,payload_json,created_at) VALUES (?,?,?,?,?,?)",
            (event.liid, seq, event.event_type, record["timestamp"],
             json.dumps(record), datetime.utcnow().isoformat())
        )

    logger.info("X2 IRI: LIID=%s seq=%d event=%s", event.liid, seq, event.event_type)
    return {"status": "accepted", "liid": event.liid, "seq": seq}


@app.get("/x2/iri/log", tags=["X2"])
def get_iri_log():
    with _lock:
        return list(reversed(_iri_events[-100:]))


# ── X3 — CC Packets from NE ──────────────────────────────────

@app.post("/x3/cc", tags=["X3"])
def receive_cc(pkt: X3Packet):
    with _lock:
        active = _warrants.get(pkt.liid, {}).get("active", False)
    if not active:
        raise HTTPException(400, f"LIID {pkt.liid} is not active")

    _seq[pkt.liid] = _seq.get(pkt.liid, 0) + 1
    record = pkt.dict()
    record["sequence_number"] = _seq[pkt.liid]
    record["received_at"] = datetime.utcnow().isoformat()

    with _lock:
        _cc_events.append(record)

    logger.info("X3 CC: LIID=%s seq=%d %s→%s", pkt.liid, record["sequence_number"], pkt.src_ip, pkt.dst_ip)
    return {"status": "accepted", "liid": pkt.liid, "seq": record["sequence_number"]}


@app.get("/x3/cc/log", tags=["X3"])
def get_cc_log():
    with _lock:
        return list(reversed(_cc_events[-100:]))


# ── X1 Tasks (portal polls these) ────────────────────────────

@app.get("/x1/tasks/{ne}", tags=["X1"])
def get_ne_tasks(ne: str):
    ne = ne.upper()
    if ne not in _ne_tasks:
        raise HTTPException(404, f"Unknown NE: {ne}")
    with _lock:
        return list(reversed(_ne_tasks[ne]))


@app.delete("/x1/tasks/{ne}", tags=["X1"])
def clear_ne_tasks(ne: str):
    ne = ne.upper()
    with _lock:
        _ne_tasks[ne] = []
    return {"cleared": ne}


# ── Health & Portal ───────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    return {
        "status": "ok",
        "active_warrants": len([w for w in _warrants.values() if w.get("active")]),
        "iri_events": len(_iri_events),
        "cc_events": len(_cc_events),
    }

@app.get("/", include_in_schema=False)
def serve_portal():
    return FileResponse("portal/index.html")


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LIS Standalone Server")
    parser.add_argument("--port", type=int, default=8001, help="Port (default: 8001)")
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    db_init()

    print("""
╔══════════════════════════════════════════════════════╗
║         LIS Standalone Server — Starting             ║
╠══════════════════════════════════════════════════════╣
║  Portal:   http://localhost:{port}                   ║
║  API docs: http://localhost:{port}/docs              ║
╠══════════════════════════════════════════════════════╣
║  All-in-one: ADMF + IRI-MF + CC-MF + NE Simulator   ║
║  Storage:    SQLite (lis_standalone.db)              ║
║  No Docker, No Kafka, No Redis needed                ║
╚══════════════════════════════════════════════════════╝
    """.replace("{port}", str(args.port)))

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
