"""
LIS Standalone Server — Zero Docker, Zero Kafka, Zero Redis
All components in one process.

Storage backends:
  SQLite (default):    python run_standalone.py
  PostgreSQL (Ubuntu): python run_standalone.py --db-url postgresql://lis:secret@localhost/lisdb

Usage:
    pip install fastapi uvicorn
    pip install psycopg2-binary   # only if using PostgreSQL
    python run_standalone.py

Then open: portal/index.html in your browser
"""
import json
import sqlite3
import threading
import uuid
import logging
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime
from contextlib import contextmanager
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("LIS")

# ═══════════════════════════════════════════════════════════════
#  SHARED IN-MEMORY STATE
# ═══════════════════════════════════════════════════════════════

_lock = threading.Lock()

# LIID → warrant dict  (in-memory cache)
_warrants: dict[str, dict] = {}

# X1 tasks received by each mock NE
_ne_tasks: dict[str, list] = {"MME": [], "SGW": [], "PGW": []}

# IRI events received via X2
_iri_events: list[dict] = []

# CC packets received via X3
_cc_events: list[dict] = []

# Sequence counters per LIID
_seq: dict[str, int] = {}


# ═══════════════════════════════════════════════════════════════
#  DATABASE LAYER  (SQLite by default, PostgreSQL optional)
# ═══════════════════════════════════════════════════════════════

DB_URL: str = ""   # set by --db-url; empty = SQLite


def _is_pg() -> bool:
    return DB_URL.startswith("postgresql") or DB_URL.startswith("postgres")


# ── SQLite helpers ────────────────────────────────────────────

SQLITE_PATH = "lis_standalone.db"


def _sqlite_init():
    con = sqlite3.connect(SQLITE_PATH)
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
    con.execute("""
        CREATE TABLE IF NOT EXISTS cc_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            liid         TEXT,
            seq          INTEGER,
            direction    TEXT,
            src_ip       TEXT,
            dst_ip       TEXT,
            payload_json TEXT,
            created_at   TEXT
        )
    """)
    con.commit()
    con.close()
    logger.info("SQLite DB ready: %s", SQLITE_PATH)


@contextmanager
def _sqlite_conn():
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


# ── PostgreSQL helpers ────────────────────────────────────────

_pg_pool = None   # psycopg2 SimpleConnectionPool


def _pg_init():
    global _pg_pool
    try:
        import psycopg2
        from psycopg2 import pool as pgpool
        from psycopg2.extras import RealDictCursor
        _pg_pool = pgpool.SimpleConnectionPool(1, 10, DB_URL)

        with _pg_conn() as con:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS warrants (
                    liid             TEXT PRIMARY KEY,
                    lea_id           TEXT,
                    target_id_type   TEXT,
                    target_value     TEXT,
                    intercept_type   TEXT,
                    delivery_address TEXT,
                    valid_from       TEXT,
                    valid_until      TEXT,
                    active           BOOLEAN DEFAULT TRUE,
                    created_at       TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS iri_log (
                    id           SERIAL PRIMARY KEY,
                    liid         TEXT,
                    seq          INTEGER,
                    event_type   TEXT,
                    timestamp    TEXT,
                    payload_json TEXT,
                    created_at   TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cc_log (
                    id           SERIAL PRIMARY KEY,
                    liid         TEXT,
                    seq          INTEGER,
                    direction    TEXT,
                    src_ip       TEXT,
                    dst_ip       TEXT,
                    payload_json TEXT,
                    created_at   TEXT
                )
            """)
        logger.info("PostgreSQL DB ready: %s", DB_URL.split("@")[-1])
    except ImportError:
        raise RuntimeError("psycopg2-binary not installed. Run: pip install psycopg2-binary")
    except Exception as e:
        raise RuntimeError(f"Cannot connect to PostgreSQL: {e}")


@contextmanager
def _pg_conn():
    from psycopg2.extras import RealDictCursor
    con = _pg_pool.getconn()
    con.autocommit = False
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        _pg_pool.putconn(con)


# ── Unified interface ────────────────────────────────────────

def db_init():
    if _is_pg():
        _pg_init()
    else:
        _sqlite_init()


@contextmanager
def db():
    if _is_pg():
        with _pg_conn() as con:
            yield _PgWrapper(con)
    else:
        with _sqlite_conn() as con:
            yield _SqliteWrapper(con)


class _SqliteWrapper:
    """Thin wrapper that normalises sqlite3.Row → dict."""
    def __init__(self, con): self._c = con

    def execute(self, sql, params=()):
        return self._c.execute(sql, params)

    def fetchone(self, sql, params=()):
        row = self._c.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql, params=()):
        rows = self._c.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def insert(self, sql, params=()):
        self._c.execute(sql, params)

    def update(self, sql, params=()):
        self._c.execute(sql, params)


class _PgWrapper:
    """Thin wrapper that uses RealDictCursor and %s placeholders."""
    def __init__(self, con):
        from psycopg2.extras import RealDictCursor
        self._c = con
        self._cursor_factory = RealDictCursor

    def _cur(self):
        from psycopg2.extras import RealDictCursor
        return self._c.cursor(cursor_factory=RealDictCursor)

    def execute(self, sql, params=()):
        sql = self._pg_sql(sql)
        cur = self._cur()
        cur.execute(sql, params)
        return cur

    def fetchone(self, sql, params=()):
        sql = self._pg_sql(sql)
        cur = self._cur()
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql, params=()):
        sql = self._pg_sql(sql)
        cur = self._cur()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [dict(r) for r in rows]

    def insert(self, sql, params=()):
        self.execute(sql, params)

    def update(self, sql, params=()):
        self.execute(sql, params)

    @staticmethod
    def _pg_sql(sql: str) -> str:
        """Convert ? placeholders to %s for psycopg2."""
        return sql.replace("?", "%s")


# ── Warm up in-memory cache from DB on startup ───────────────

def _load_warrants_from_db():
    """Restore active warrants into _warrants dict on startup."""
    with db() as d:
        rows = d.fetchall(
            "SELECT * FROM warrants WHERE active=1 AND valid_until > ?",
            (datetime.utcnow().isoformat(),)
        )
    with _lock:
        for r in rows:
            _warrants[r["liid"]] = {**r, "active": True}
    logger.info("Loaded %d active warrants from DB", len(rows))


# ═══════════════════════════════════════════════════════════════
#  MAIN APP  (ADMF HI1 + X2 IRI receiver + X3 CC receiver)
# ═══════════════════════════════════════════════════════════════

app = FastAPI(title="LIS Standalone", version="2.0.0")
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
    itype = warrant.get("intercept_type", "IRI_AND_CC")
    ne_targets = {
        "IRI_ONLY":   ["MME"],
        "CC_ONLY":    ["SGW", "PGW"],
        "IRI_AND_CC": ["MME", "SGW", "PGW"],
    }.get(itype, ["MME", "SGW", "PGW"])

    for ne in ne_targets:
        task = {
            "task_id":       str(uuid.uuid4()),
            "liid":          warrant["liid"],
            "target_id_type":warrant.get("target_id_type", "IMSI"),
            "target_value":  warrant.get("target_value", ""),
            "intercept_type":itype,
            "action":        action,
            "ne_name":       ne,
            "received_at":   datetime.utcnow().isoformat(),
        }
        with _lock:
            _ne_tasks[ne].append(task)
        logger.info("X1 [%s] %s → %s target=%s", ne, action, warrant["liid"], warrant.get("target_value"))


@app.post("/hi1/warrants/activate", tags=["HI1"])
def activate_warrant(req: ActivateReq):
    with db() as d:
        existing = d.fetchone("SELECT liid FROM warrants WHERE liid=?", (req.liid,))
        if existing:
            raise HTTPException(409, f"LIID {req.liid} already exists")
        now = datetime.utcnow().isoformat()
        d.insert("""
            INSERT INTO warrants
              (liid,lea_id,target_id_type,target_value,intercept_type,
               delivery_address,valid_from,valid_until,active,created_at)
            VALUES (?,?,?,?,?,?,?,?,1,?)
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
    with db() as d:
        row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (req.liid,))
        if not row:
            raise HTTPException(404, f"Warrant {req.liid} not found")
        if row["lea_id"] != req.lea_id:
            raise HTTPException(403, "LEA ID mismatch")
        d.update("UPDATE warrants SET active=0 WHERE liid=?", (req.liid,))

    with _lock:
        if req.liid in _warrants:
            _warrants[req.liid]["active"] = False
        warrant = row

    _x1_provision(warrant, "DEACTIVATE")
    logger.info("HI1 DEACTIVATE: LIID=%s", req.liid)
    return {"liid": req.liid, "status": "DEACTIVATED", "message": "Intercept deactivated"}


@app.get("/hi1/warrants", tags=["HI1"])
def list_warrants():
    with db() as d:
        rows = d.fetchall(
            "SELECT * FROM warrants WHERE active=1 AND valid_until > ? ORDER BY created_at DESC",
            (datetime.utcnow().isoformat(),)
        )
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
    with db() as d:
        row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (liid,))
    if not row:
        raise HTTPException(404, f"Warrant {liid} not found")
    return row


# ── HI1 SOAP — ETSI TS 103 120 (real protocol) ───────────────
#
#  Real 4G/LTE networks use SOAP 1.1 over HTTPS for HI1.
#  This endpoint accepts the actual SOAP/XML envelope defined in
#  ETSI TS 103 120 and processes it identically to the REST API.
#
#  Operations supported:
#    ActivateInterceptionRequest
#    DeactivateInterceptionRequest
#    GetInterceptionStatusRequest

SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
HI1_NS      = "urn:etsi:103120:hi1:2019"


def _soap_ok(operation: str, liid: str, status: str, message: str = "") -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope
    xmlns:soapenv="{SOAP_ENV_NS}"
    xmlns:hi1="{HI1_NS}">
  <soapenv:Header/>
  <soapenv:Body>
    <hi1:{operation}Response>
      <hi1:LIID>{liid}</hi1:LIID>
      <hi1:Result>{status}</hi1:Result>
      <hi1:Message>{message}</hi1:Message>
      <hi1:Timestamp>{datetime.utcnow().isoformat()}Z</hi1:Timestamp>
    </hi1:{operation}Response>
  </soapenv:Body>
</soapenv:Envelope>"""


def _soap_fault(code: str, reason: str) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}">
  <soapenv:Body>
    <soapenv:Fault>
      <faultcode>{code}</faultcode>
      <faultstring>{reason}</faultstring>
    </soapenv:Fault>
  </soapenv:Body>
</soapenv:Envelope>"""


def _xml(el, tag: str) -> str:
    """Helper: get text of a child element (with or without namespace)."""
    node = el.find(f"{{{HI1_NS}}}{tag}") or el.find(tag)
    return node.text.strip() if node is not None and node.text else ""


@app.post("/hi1/soap", tags=["HI1-SOAP"],
          summary="HI1 SOAP endpoint — ETSI TS 103 120",
          response_class=Response)
async def hi1_soap(request: Request):
    """
    Accepts SOAP 1.1 / XML over HTTP(S).
    Content-Type: text/xml  or  application/soap+xml
    SOAPAction header must be set to the operation name.
    """
    raw = await request.body()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        xml_out = _soap_fault("soapenv:Client", f"Malformed XML: {e}")
        return Response(content=xml_out, media_type="text/xml", status_code=400)

    soap_body = root.find(f"{{{SOAP_ENV_NS}}}Body")
    if soap_body is None:
        xml_out = _soap_fault("soapenv:Client", "Missing SOAP Body")
        return Response(content=xml_out, media_type="text/xml", status_code=400)

    # ── ACTIVATE ─────────────────────────────────────────────
    req_el = soap_body.find(f"{{{HI1_NS}}}ActivateInterceptionRequest") \
          or soap_body.find("ActivateInterceptionRequest")
    if req_el is not None:
        liid    = _xml(req_el, "LIID")
        lea_id  = _xml(req_el, "LEAID")
        itype   = "IRI_AND_CC"

        # InterceptionType block
        itype_el = req_el.find(f"{{{HI1_NS}}}InterceptionType") \
                or req_el.find("InterceptionType")
        if itype_el is not None:
            iri_flag = (_xml(itype_el, "IRI").lower() == "true")
            cc_flag  = (_xml(itype_el, "CC").lower()  == "true")
            if iri_flag and cc_flag:   itype = "IRI_AND_CC"
            elif iri_flag:             itype = "IRI_ONLY"
            elif cc_flag:              itype = "CC_ONLY"

        # TargetIdentifiers block
        tgt_el = req_el.find(f"{{{HI1_NS}}}TargetIdentifiers") \
              or req_el.find("TargetIdentifiers")
        id_type, id_val = "IMSI", ""
        if tgt_el is not None:
            tid = tgt_el.find(f"{{{HI1_NS}}}TargetIdentifier") \
               or tgt_el.find("TargetIdentifier")
            if tid is not None:
                for tag in ("IMSI", "MSISDN", "IMEI"):
                    v = _xml(tid, tag)
                    if v:
                        id_type, id_val = tag, v
                        break

        # DeliveryAddress block
        del_el = req_el.find(f"{{{HI1_NS}}}DeliveryAddress") \
              or req_el.find("DeliveryAddress")
        delivery = "127.0.0.1:8443"
        if del_el is not None:
            ip   = _xml(del_el, "IPAddress")
            port = _xml(del_el, "Port")
            if ip and port:
                delivery = f"{ip}:{port}"

        valid_from     = _xml(req_el, "ValidFrom")             or datetime.utcnow().isoformat()
        valid_until    = _xml(req_el, "ValidUntil")            or "2099-12-31T23:59:59Z"
        legal_auth     = _xml(req_el, "LegalAuthority")        or "MHA — Government of India"
        auth_ref       = _xml(req_el, "AuthorizationReference") or "Section 5(2) Telegraph Act 1885"
        country_code   = _xml(req_el, "AuthorizationCountryCode") or "IN"
        logger.info("HI1-SOAP legal: country=%s authority=%s ref=%s", country_code, legal_auth, auth_ref)

        # Reuse the existing REST logic
        from fastapi.testclient import TestClient   # type: ignore
        try:
            activate_warrant(ActivateReq(
                liid=liid, lea_id=lea_id,
                target=TargetIn(id_type=id_type, value=id_val),
                intercept_type=itype,
                valid_from=valid_from, valid_until=valid_until,
                delivery_address=delivery,
            ))
            xml_out = _soap_ok("ActivateInterception", liid, "SUCCESS",
                               f"Intercept activated for {id_type}={id_val}")
            logger.info("HI1-SOAP ACTIVATE: LIID=%s target=%s(%s)", liid, id_val, id_type)
            return Response(content=xml_out, media_type="text/xml")
        except HTTPException as e:
            xml_out = _soap_fault("soapenv:Server", e.detail)
            return Response(content=xml_out, media_type="text/xml", status_code=e.status_code)

    # ── DEACTIVATE ───────────────────────────────────────────
    req_el = soap_body.find(f"{{{HI1_NS}}}DeactivateInterceptionRequest") \
          or soap_body.find("DeactivateInterceptionRequest")
    if req_el is not None:
        liid   = _xml(req_el, "LIID")
        lea_id = _xml(req_el, "LEAID")
        try:
            deactivate_warrant(DeactivateReq(liid=liid, lea_id=lea_id))
            xml_out = _soap_ok("DeactivateInterception", liid, "SUCCESS", "Intercept deactivated")
            logger.info("HI1-SOAP DEACTIVATE: LIID=%s", liid)
            return Response(content=xml_out, media_type="text/xml")
        except HTTPException as e:
            xml_out = _soap_fault("soapenv:Server", e.detail)
            return Response(content=xml_out, media_type="text/xml", status_code=e.status_code)

    # ── GET STATUS ───────────────────────────────────────────
    req_el = soap_body.find(f"{{{HI1_NS}}}GetInterceptionStatusRequest") \
          or soap_body.find("GetInterceptionStatusRequest")
    if req_el is not None:
        liid = _xml(req_el, "LIID")
        with db() as d:
            row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (liid,))
        if not row:
            xml_out = _soap_fault("soapenv:Server", f"Warrant {liid} not found")
            return Response(content=xml_out, media_type="text/xml", status_code=404)
        active_str = "ACTIVE" if row.get("active") else "INACTIVE"
        xml_out = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}" xmlns:hi1="{HI1_NS}">
  <soapenv:Body>
    <hi1:GetInterceptionStatusResponse>
      <hi1:LIID>{liid}</hi1:LIID>
      <hi1:Status>{active_str}</hi1:Status>
      <hi1:InterceptionType>{row.get('intercept_type','')}</hi1:InterceptionType>
      <hi1:ValidFrom>{row.get('valid_from','')}</hi1:ValidFrom>
      <hi1:ValidUntil>{row.get('valid_until','')}</hi1:ValidUntil>
      <hi1:TargetIdentifier>{row.get('target_value','')}</hi1:TargetIdentifier>
    </hi1:GetInterceptionStatusResponse>
  </soapenv:Body>
</soapenv:Envelope>"""
        return Response(content=xml_out, media_type="text/xml")

    # Unknown operation
    xml_out = _soap_fault("soapenv:Client", "Unknown or unsupported SOAP operation")
    return Response(content=xml_out, media_type="text/xml", status_code=400)


# ── X2 — IRI Events from NE ──────────────────────────────────

@app.post("/x2/iri", tags=["X2"])
def receive_iri(event: X2Event):
    with _lock:
        active = _warrants.get(event.liid, {}).get("active", False)
    if not active:
        # Also check DB (warrant loaded before this process started)
        with db() as d:
            row = d.fetchone("SELECT active FROM warrants WHERE liid=?", (event.liid,))
        active = bool(row and row.get("active"))
        if active:
            with _lock:
                _warrants[event.liid] = {"active": True}
    if not active:
        logger.warning("X2: unknown/inactive LIID=%s — discarding", event.liid)
        raise HTTPException(400, f"LIID {event.liid} is not active")

    with _lock:
        _seq[event.liid] = _seq.get(event.liid, 0) + 1
        seq = _seq[event.liid]

    record = event.dict()
    record["sequence_number"] = seq
    record["timestamp"] = record.get("timestamp") or datetime.utcnow().isoformat()
    record["received_at"] = datetime.utcnow().isoformat()

    with _lock:
        _iri_events.append(record)

    with db() as d:
        d.insert(
            "INSERT INTO iri_log (liid,seq,event_type,timestamp,payload_json,created_at) VALUES (?,?,?,?,?,?)",
            (event.liid, seq, event.event_type, record["timestamp"],
             json.dumps(record), datetime.utcnow().isoformat())
        )

    logger.info("X2 IRI: LIID=%s seq=%d event=%s", event.liid, seq, event.event_type)
    return {"status": "accepted", "liid": event.liid, "seq": seq}


@app.get("/x2/iri/log", tags=["X2"])
def get_iri_log():
    """Returns last 100 IRI events (newest first)."""
    with _lock:
        return list(reversed(_iri_events[-100:]))


@app.get("/x2/iri/log/db", tags=["X2"])
def get_iri_log_db(liid: Optional[str] = None, limit: int = 100):
    """Query IRI log from database (survives restarts)."""
    if liid:
        sql = "SELECT * FROM iri_log WHERE liid=? ORDER BY id DESC LIMIT ?"
        params = (liid, limit)
    else:
        sql = "SELECT * FROM iri_log ORDER BY id DESC LIMIT ?"
        params = (limit,)
    with db() as d:
        return d.fetchall(sql, params)


# ── X3 — CC Packets from NE ──────────────────────────────────

@app.post("/x3/cc", tags=["X3"])
def receive_cc(pkt: X3Packet):
    with _lock:
        active = _warrants.get(pkt.liid, {}).get("active", False)
    if not active:
        raise HTTPException(400, f"LIID {pkt.liid} is not active")

    with _lock:
        _seq[pkt.liid] = _seq.get(pkt.liid, 0) + 1
        seq = _seq[pkt.liid]

    record = pkt.dict()
    record["sequence_number"] = seq
    record["received_at"] = datetime.utcnow().isoformat()

    with _lock:
        _cc_events.append(record)

    with db() as d:
        d.insert(
            "INSERT INTO cc_log (liid,seq,direction,src_ip,dst_ip,payload_json,created_at) VALUES (?,?,?,?,?,?,?)",
            (pkt.liid, seq, pkt.direction, pkt.src_ip, pkt.dst_ip,
             json.dumps(record), datetime.utcnow().isoformat())
        )

    logger.info("X3 CC: LIID=%s seq=%d %s→%s", pkt.liid, seq, pkt.src_ip, pkt.dst_ip)
    return {"status": "accepted", "liid": pkt.liid, "seq": seq}


@app.get("/x3/cc/log", tags=["X3"])
def get_cc_log():
    with _lock:
        return list(reversed(_cc_events[-100:]))


@app.get("/x3/cc/log/db", tags=["X3"])
def get_cc_log_db(liid: Optional[str] = None, limit: int = 100):
    """Query CC log from database (survives restarts)."""
    if liid:
        sql = "SELECT * FROM cc_log WHERE liid=? ORDER BY id DESC LIMIT ?"
        params = (liid, limit)
    else:
        sql = "SELECT * FROM cc_log ORDER BY id DESC LIMIT ?"
        params = (limit,)
    with db() as d:
        return d.fetchall(sql, params)


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


# ── Health & Stats ───────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    db_type = "postgresql" if _is_pg() else "sqlite"
    with db() as d:
        total_warrants = (d.fetchone("SELECT COUNT(*) AS c FROM warrants") or {}).get("c", 0)
        total_iri      = (d.fetchone("SELECT COUNT(*) AS c FROM iri_log") or {}).get("c", 0)
        total_cc       = (d.fetchone("SELECT COUNT(*) AS c FROM cc_log") or {}).get("c", 0)
    return {
        "status":           "ok",
        "db":               db_type,
        "active_warrants":  len([w for w in _warrants.values() if w.get("active")]),
        "iri_events_mem":   len(_iri_events),
        "cc_events_mem":    len(_cc_events),
        "total_warrants_db":total_warrants,
        "total_iri_db":     total_iri,
        "total_cc_db":      total_cc,
    }


@app.get("/", include_in_schema=False)
def serve_portal():
    return FileResponse("portal/index.html")


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LIS Standalone Server")
    parser.add_argument("--port",   type=int, default=8001, help="Port (default: 8001)")
    parser.add_argument("--host",   default="0.0.0.0")
    parser.add_argument("--db-url", default="",
        help="PostgreSQL URL e.g. postgresql://lis:secret@localhost/lisdb  (default: SQLite)")
    args = parser.parse_args()

    global DB_URL
    DB_URL = args.db_url

    db_init()
    _load_warrants_from_db()

    db_label = f"PostgreSQL ({args.db_url.split('@')[-1]})" if _is_pg() else f"SQLite ({SQLITE_PATH})"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           LIS Standalone Server v2.0 — Starting             ║
╠══════════════════════════════════════════════════════════════╣
║  Portal:   http://localhost:{args.port:<5}                       ║
║  API docs: http://localhost:{args.port:<5}/docs                   ║
╠══════════════════════════════════════════════════════════════╣
║  Storage:  {db_label:<50}║
║  No Docker, No Kafka, No Redis needed                        ║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
