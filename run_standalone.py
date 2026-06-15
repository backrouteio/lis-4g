"""
LIS Standalone Server v3.0 — India 4G LTE Lawful Interception System
Zero Docker, Zero Kafka, Zero Redis. All in one process.

Interfaces:
  HI1  — SOAP/XML warrant activation (ETSI TS 103 120)
  HI2  — IRI delivery to LEA (ASN.1 BER, HTTPS push)
  HI3  — CC delivery to LEA via SFTP (paramiko)
  X1   — NE provisioning tasks (ADMF → MME/SGW/PGW)
  X2   — IRI events from NE (ASN.1 BER accepted)
  X3   — CC packets from NE (mirrored user-plane data)

Storage:
  SQLite (default):    python run_standalone.py
  PostgreSQL (Ubuntu): python run_standalone.py --db-url postgresql://lis:secret@localhost/lisdb

India context:
  Operators: Airtel 404-10, Jio 405-854, Vi 404-20, BSNL 404-07
  Legal: Telegraph Act S5(2), IT Act S69, UAPA S39, NIA Act S6
  Format: +91 MSISDN, 404/405 IMSI prefix
"""
import json
import os
import sqlite3
import threading
import uuid
import logging
import argparse
import time
import xml.etree.ElementTree as ET
from datetime import datetime
from contextlib import contextmanager
from typing import Optional, Any, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("LIS")

# ═══════════════════════════════════════════════════════════════
#  SHARED IN-MEMORY STATE
# ═══════════════════════════════════════════════════════════════

_lock = threading.Lock()
_START_TIME = time.time()

# LIID → warrant dict  (in-memory cache)
_warrants: dict[str, dict] = {}

# X1 tasks received by each NE
_ne_tasks: dict[str, list] = {"MME": [], "SGW": [], "PGW": []}

# IRI events received via X2
_iri_events: list[dict] = []

# CC packets received via X3
_cc_events: list[dict] = []

# Sequence counters per LIID
_seq: dict[str, int] = {}

# SFTP config for HI3 CC delivery
_sftp_config: dict = {}

# CC files received (for /hi3/files listing)
_cc_files: list[dict] = []


# ═══════════════════════════════════════════════════════════════
#  DATABASE LAYER  (SQLite by default, PostgreSQL optional)
# ═══════════════════════════════════════════════════════════════

DB_URL: str = ""   # set by --db-url; empty = SQLite
SQLITE_PATH = "lis_standalone.db"


def _is_pg() -> bool:
    return DB_URL.startswith("postgresql") or DB_URL.startswith("postgres")


# ── SQLite ────────────────────────────────────────────────────

def _sqlite_init():
    con = sqlite3.connect(SQLITE_PATH)
    # warrants — full India fields
    con.execute("""
        CREATE TABLE IF NOT EXISTS warrants (
            liid             TEXT PRIMARY KEY,
            lea_id           TEXT,
            target_id_type   TEXT,
            target_value     TEXT,
            target_msisdn    TEXT,
            target_imsi      TEXT,
            target_imei      TEXT,
            intercept_type   TEXT,
            legal_authority  TEXT,
            auth_ref         TEXT,
            authorized_by    TEXT,
            delivery_ip      TEXT,
            delivery_port    INTEGER,
            delivery_address TEXT,
            valid_from       TEXT,
            valid_until      TEXT,
            country_code     TEXT DEFAULT 'IN',
            active           INTEGER DEFAULT 1,
            created_at       TEXT
        )
    """)
    # Add missing columns if upgrading from v2
    for col, defn in [
        ("target_msisdn","TEXT"), ("target_imsi","TEXT"), ("target_imei","TEXT"),
        ("legal_authority","TEXT"), ("auth_ref","TEXT"), ("authorized_by","TEXT"),
        ("delivery_ip","TEXT"), ("delivery_port","INTEGER"),
        ("country_code","TEXT DEFAULT 'IN'"),
    ]:
        try:
            con.execute(f"ALTER TABLE warrants ADD COLUMN {col} {defn}")
        except Exception:
            pass  # already exists

    con.execute("""
        CREATE TABLE IF NOT EXISTS iri_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            liid         TEXT,
            seq          INTEGER,
            event_type   TEXT,
            ts           TEXT,
            ne_source    TEXT,
            imsi         TEXT,
            payload      TEXT,
            asn1_hex     TEXT,
            encoding     TEXT,
            hi2_delivered INTEGER DEFAULT 0,
            created_at   TEXT
        )
    """)
    for col, defn in [("ne_source","TEXT"), ("imsi","TEXT"), ("payload","TEXT"),
                      ("asn1_hex","TEXT"), ("encoding","TEXT"), ("hi2_delivered","INTEGER DEFAULT 0")]:
        try: con.execute(f"ALTER TABLE iri_log ADD COLUMN {col} {defn}")
        except: pass

    con.execute("""
        CREATE TABLE IF NOT EXISTS cc_log (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            liid           TEXT,
            seq            INTEGER,
            ne_source      TEXT,
            direction      TEXT,
            src_ip         TEXT,
            dst_ip         TEXT,
            payload        TEXT,
            sftp_delivered INTEGER DEFAULT 0,
            sftp_path      TEXT,
            created_at     TEXT
        )
    """)
    for col, defn in [("ne_source","TEXT"), ("payload","TEXT"),
                      ("sftp_delivered","INTEGER DEFAULT 0"), ("sftp_path","TEXT")]:
        try: con.execute(f"ALTER TABLE cc_log ADD COLUMN {col} {defn}")
        except: pass

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


# ── PostgreSQL ────────────────────────────────────────────────

_pg_pool = None


def _pg_init():
    global _pg_pool
    try:
        from psycopg2 import pool as pgpool
        _pg_pool = pgpool.SimpleConnectionPool(1, 10, DB_URL)

        with _pg_conn() as con:
            cur = con.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS warrants (
                    liid             TEXT PRIMARY KEY,
                    lea_id           TEXT,
                    target_id_type   TEXT,
                    target_value     TEXT,
                    target_msisdn    TEXT,
                    target_imsi      TEXT,
                    target_imei      TEXT,
                    intercept_type   TEXT,
                    legal_authority  TEXT,
                    auth_ref         TEXT,
                    authorized_by    TEXT,
                    delivery_ip      TEXT,
                    delivery_port    INTEGER,
                    delivery_address TEXT,
                    valid_from       TEXT,
                    valid_until      TEXT,
                    country_code     TEXT DEFAULT 'IN',
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
                    ts           TEXT,
                    ne_source    TEXT,
                    imsi         TEXT,
                    payload      TEXT,
                    asn1_hex     TEXT,
                    encoding     TEXT,
                    hi2_delivered BOOLEAN DEFAULT FALSE,
                    created_at   TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS cc_log (
                    id             SERIAL PRIMARY KEY,
                    liid           TEXT,
                    seq            INTEGER,
                    ne_source      TEXT,
                    direction      TEXT,
                    src_ip         TEXT,
                    dst_ip         TEXT,
                    payload        TEXT,
                    sftp_delivered BOOLEAN DEFAULT FALSE,
                    sftp_path      TEXT,
                    created_at     TEXT
                )
            """)
        logger.info("PostgreSQL DB ready: %s", DB_URL.split("@")[-1])
    except ImportError:
        raise RuntimeError("psycopg2-binary not installed. Run: pip install psycopg2-binary --break-system-packages")
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


# ── Unified DB interface ───────────────────────────────────────

def db_init():
    if _is_pg(): _pg_init()
    else:         _sqlite_init()


@contextmanager
def db():
    if _is_pg():
        with _pg_conn() as con: yield _PgWrapper(con)
    else:
        with _sqlite_conn() as con: yield _SqliteWrapper(con)


class _SqliteWrapper:
    def __init__(self, con): self._c = con
    def execute(self, sql, params=()):   return self._c.execute(sql, params)
    def fetchone(self, sql, params=()):
        row = self._c.execute(sql, params).fetchone()
        return dict(row) if row else None
    def fetchall(self, sql, params=()):
        return [dict(r) for r in self._c.execute(sql, params).fetchall()]
    def insert(self, sql, params=()): self._c.execute(sql, params)
    def update(self, sql, params=()): self._c.execute(sql, params)


class _PgWrapper:
    def __init__(self, con):
        from psycopg2.extras import RealDictCursor
        self._c = con; self._rcf = RealDictCursor
    def _cur(self):
        from psycopg2.extras import RealDictCursor
        return self._c.cursor(cursor_factory=RealDictCursor)
    def _s(self, sql): return sql.replace("?", "%s")
    def execute(self, sql, params=()):
        c=self._cur(); c.execute(self._s(sql), params); return c
    def fetchone(self, sql, params=()):
        c=self._cur(); c.execute(self._s(sql), params)
        row=c.fetchone(); return dict(row) if row else None
    def fetchall(self, sql, params=()):
        c=self._cur(); c.execute(self._s(sql), params)
        return [dict(r) for r in c.fetchall()]
    def insert(self, sql, params=()): self.execute(sql, params)
    def update(self, sql, params=()): self.execute(sql, params)


# ── Restore warrants from DB on startup ───────────────────────

def _load_warrants_from_db():
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
#  ASN.1 BER HELPERS  (simple decoder — 3GPP TS 33.108)
# ═══════════════════════════════════════════════════════════════

IRI_TYPE_NAMES = {0:"ATTACH",1:"DETACH",2:"BEARER_ESTABLISH",3:"BEARER_RELEASE",
                  4:"LOCATION_UPDATE",5:"SMS",6:"TAU",7:"HANDOVER"}

def _ber_decode_iri(hex_str: str) -> dict:
    """
    Decode a simplified 3GPP TS 33.108 IRIParameters ASN.1 BER structure.
    Returns a dict of decoded fields.
    """
    try:
        data = bytes.fromhex(hex_str)
        result = {}
        i = 0
        if i >= len(data) or data[i] != 0x30:  # SEQUENCE tag
            return {"decode_error": "Not a SEQUENCE"}
        i += 1
        # skip length
        if data[i] & 0x80:
            n_len_bytes = data[i] & 0x7f; i += 1 + n_len_bytes
        else:
            i += 1
        # parse context-tagged elements [0]..[12]
        while i < len(data):
            if i >= len(data): break
            tag = data[i]; i += 1
            if i >= len(data): break
            # length
            if data[i] & 0x80:
                n = data[i] & 0x7f; i += 1
                length = int.from_bytes(data[i:i+n], 'big'); i += n
            else:
                length = data[i]; i += 1
            value = data[i:i+length]; i += length
            tag_num = tag & 0x1f
            # skip inner TLV wrapper (context primitive wraps universal)
            def inner_val(v):
                if not v: return v
                # skip one level of wrapping: skip tag+length
                j = 1
                if len(v) <= 1: return v
                if v[j] & 0x80:
                    nb = v[j] & 0x7f; j += 1 + nb
                else:
                    j += 1
                return v[j:]
            iv = inner_val(value)
            if tag_num == 0:   result["iriVersion"] = int.from_bytes(iv, 'big') if iv else 0
            elif tag_num == 1: result["timeStamp"] = iv.decode('ascii', errors='replace')
            elif tag_num == 2: result["liID"] = iv.decode('utf-8', errors='replace')
            elif tag_num == 3: result["sequenceNumber"] = int.from_bytes(iv, 'big') if iv else 0
            elif tag_num == 4:
                iri_type_val = iv[-1] if iv else 0
                result["iriType"] = IRI_TYPE_NAMES.get(iri_type_val, f"unknown({iri_type_val})")
            elif tag_num == 5:  result["targetIMSI"]    = iv.decode('utf-8', errors='replace')
            elif tag_num == 6:  result["targetMSISDN"]  = iv.decode('utf-8', errors='replace')
            elif tag_num == 7:  result["cellID"]        = iv.decode('utf-8', errors='replace')
            elif tag_num == 8:  result["tai"]           = iv.decode('utf-8', errors='replace')
            elif tag_num == 9:  result["ueIPAddress"]   = iv.decode('utf-8', errors='replace')
            elif tag_num == 10: result["apn"]           = iv.decode('utf-8', errors='replace')
            elif tag_num == 11: result["bearerID"]      = int.from_bytes(iv, 'big') if iv else 0
            elif tag_num == 12: result["qci"]           = int.from_bytes(iv, 'big') if iv else 0
        return result
    except Exception as e:
        return {"decode_error": str(e)}


# ═══════════════════════════════════════════════════════════════
#  SFTP DELIVERY  (HI3 CC content to LEA)
# ═══════════════════════════════════════════════════════════════

CC_RECEIVED_DIR = "cc_received"

def _ensure_cc_dir():
    os.makedirs(CC_RECEIVED_DIR, exist_ok=True)

def _deliver_sftp(liid: str, seq: int, payload: dict) -> tuple[bool, str]:
    """
    Deliver CC packet to LEA SFTP server.
    Creates a binary file with the CC content and uploads it.
    Returns (success, remote_path).
    """
    if not _sftp_config.get("host"):
        return False, ""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(
            hostname=_sftp_config["host"],
            port=int(_sftp_config.get("port", 2222)),
            username=_sftp_config.get("username", "lea"),
            password=_sftp_config.get("password", ""),
            timeout=10,
        )
        sftp = client.open_sftp()
        # Build CC file content
        cc_data = json.dumps({
            "liid": liid, "seq": seq,
            "timestamp": datetime.utcnow().isoformat(),
            "ne_source": payload.get("ne_source", ""),
            "payload": payload,
        }, indent=2).encode("utf-8")
        remote_dir  = "cc_received"
        fname       = f"cc_{liid}_{seq:06d}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
        remote_path = f"{remote_dir}/{fname}"
        # Ensure remote dir exists
        try: sftp.mkdir(remote_dir)
        except: pass
        import io
        sftp.putfo(io.BytesIO(cc_data), remote_path)
        sftp.close(); client.close()
        # Track locally too
        _ensure_cc_dir()
        local_path = os.path.join(CC_RECEIVED_DIR, fname)
        with open(local_path, "wb") as f:
            f.write(cc_data)
        with _lock:
            _cc_files.append({"name": fname, "liid": liid, "size": f"{len(cc_data)}B", "ts": datetime.utcnow().isoformat()})
        logger.info("HI3 SFTP: delivered %s → %s:%s/%s", liid, _sftp_config["host"], _sftp_config.get("port",2222), remote_path)
        return True, remote_path
    except ImportError:
        logger.warning("paramiko not installed — SFTP delivery skipped. pip install paramiko --break-system-packages")
        return False, "paramiko_not_installed"
    except Exception as e:
        logger.warning("HI3 SFTP failed: %s", e)
        return False, f"error: {e}"


# ═══════════════════════════════════════════════════════════════
#  HI2 PUSH DELIVERY  (IRI events to LEA HTTPS)
# ═══════════════════════════════════════════════════════════════

def _deliver_hi2(liid: str, iri_record: dict, delivery_ip: str, delivery_port: int):
    """
    Push IRI event to LEA HI2 endpoint via HTTPS.
    Non-blocking — runs in background thread.
    """
    if not delivery_ip:
        return
    url = f"http://{delivery_ip}:{delivery_port}/hi2/iri"  # LEA receiver endpoint
    try:
        import urllib.request
        data = json.dumps(iri_record).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        logger.info("HI2 push: delivered LIID=%s to %s", liid, url)
    except Exception as e:
        logger.debug("HI2 push failed (non-critical): %s", e)


def _hi2_push_async(liid: str, iri_record: dict):
    """Find the warrant's delivery address and push IRI in background."""
    warrant = {}
    with _lock:
        warrant = dict(_warrants.get(liid, {}))
    delivery_ip = warrant.get("delivery_ip", "")
    delivery_port = warrant.get("delivery_port", 8443)
    if not delivery_ip:
        # Try to parse from delivery_address field
        da = warrant.get("delivery_address", "")
        if ":" in da:
            parts = da.rsplit(":", 1)
            delivery_ip = parts[0]
            try: delivery_port = int(parts[1])
            except: pass
    if delivery_ip:
        t = threading.Thread(target=_deliver_hi2, args=(liid, iri_record, delivery_ip, delivery_port), daemon=True)
        t.start()


# ═══════════════════════════════════════════════════════════════
#  FASTAPI APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="LIS Standalone — India 4G LTE",
    version="3.0.0",
    description="Lawful Interception System — DoT/MHA Authorised | HI1/HI2/HI3/X1/X2/X3"
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Pydantic Models ───────────────────────────────────────────

class TargetIn(BaseModel):
    id_type: str = "IMSI"
    value: str = ""

class ActivateReq(BaseModel):
    liid: str
    lea_id: str
    target: Optional[TargetIn] = None
    # Flat fields (from LEA portal / SOAP parser)
    target_msisdn:   Optional[str] = None
    target_imsi:     Optional[str] = None
    target_imei:     Optional[str] = None
    intercept_type:  str = "IRI_AND_CC"
    legal_authority: Optional[str] = None
    auth_ref:        Optional[str] = None
    authorized_by:   Optional[str] = None
    delivery_ip:     Optional[str] = None
    delivery_port:   Optional[int] = 8443
    delivery_address: Optional[str] = None
    valid_from:      str = ""
    valid_until:     str = "2099-12-31T23:59:59Z"
    country_code:    str = "IN"

class DeactivateReq(BaseModel):
    liid: str
    lea_id: str

class X2IriEvent(BaseModel):
    liid:       str
    event_type: str
    ne_source:  Optional[str] = "MME"
    payload:    Optional[Any] = None   # dict of IRI fields
    asn1_hex:   Optional[str] = None   # ASN.1 BER hex
    encoding:   Optional[str] = "JSON"  # ASN1_BER or JSON
    seq_no:     Optional[int] = None
    timestamp:  Optional[str] = None
    # legacy flat fields
    imsi:  Optional[str] = None
    msisdn:Optional[str] = None
    cell_id:Optional[str]= None
    tai:   Optional[str] = None
    apn:   Optional[str] = None
    ue_ip: Optional[str] = None

class X3CcPacket(BaseModel):
    liid:        str
    ne_source:   Optional[str] = "SGW"
    payload:     Optional[Any] = None   # dict of CC fields
    deliver_sftp: bool = True
    direction:   Optional[str] = "UPLINK"
    # legacy flat fields
    src_ip:  Optional[str] = None
    dst_ip:  Optional[str] = None
    src_port:Optional[int] = None
    dst_port:Optional[int] = None

class SftpConfigReq(BaseModel):
    host:     str
    port:     int = 2222
    username: str = "lea"
    password: str = ""


# ── HI1 — Warrant Management ─────────────────────────────────

def _x1_provision(warrant: dict, action: str):
    """Push X1 task to relevant NEs based on intercept type."""
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
            "lea_id":        warrant.get("lea_id", ""),
            "target_msisdn": warrant.get("target_msisdn") or warrant.get("target_value", ""),
            "target_imsi":   warrant.get("target_imsi") or "",
            "target_imei":   warrant.get("target_imei") or "",
            "intercept_type":itype,
            "delivery_ip":   warrant.get("delivery_ip", ""),
            "delivery_port": warrant.get("delivery_port", 8443),
            "action":        action,
            "ne_name":       ne,
            "received_at":   datetime.utcnow().isoformat(),
            "valid_until":   warrant.get("valid_until", ""),
            "legal_authority": warrant.get("legal_authority", ""),
        }
        with _lock:
            if action == "DEACTIVATE":
                # Remove matching task from NE queue
                _ne_tasks[ne] = [t for t in _ne_tasks[ne] if t.get("liid") != warrant["liid"]]
            else:
                _ne_tasks[ne].append(task)
        logger.info("X1 [%s] %s → %s target=%s", ne, action, warrant["liid"], task["target_msisdn"])


@app.post("/hi1/warrants/activate", tags=["HI1"])
def activate_warrant(req: ActivateReq):
    # Normalise target fields
    target_msisdn = req.target_msisdn or (req.target.value if req.target and req.target.id_type=="MSISDN" else None) or ""
    target_imsi   = req.target_imsi   or (req.target.value if req.target and req.target.id_type=="IMSI"   else None) or ""
    target_imei   = req.target_imei   or (req.target.value if req.target and req.target.id_type=="IMEI"   else None) or ""
    target_value  = target_imsi or target_msisdn
    target_type   = "IMSI" if target_imsi else "MSISDN"
    delivery_ip   = req.delivery_ip or ""
    delivery_port = req.delivery_port or 8443
    delivery_addr = req.delivery_address or (f"{delivery_ip}:{delivery_port}" if delivery_ip else "")

    with db() as d:
        existing = d.fetchone("SELECT liid FROM warrants WHERE liid=?", (req.liid,))
        if existing:
            # Update existing
            d.update("UPDATE warrants SET active=1 WHERE liid=?", (req.liid,))
        else:
            now = datetime.utcnow().isoformat()
            d.insert("""
                INSERT INTO warrants
                  (liid,lea_id,target_id_type,target_value,target_msisdn,target_imsi,target_imei,
                   intercept_type,legal_authority,auth_ref,authorized_by,
                   delivery_ip,delivery_port,delivery_address,
                   valid_from,valid_until,country_code,active,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
            """, (req.liid, req.lea_id, target_type, target_value,
                  target_msisdn, target_imsi, target_imei,
                  req.intercept_type, req.legal_authority, req.auth_ref, req.authorized_by,
                  delivery_ip, delivery_port, delivery_addr,
                  req.valid_from, req.valid_until, req.country_code,
                  now))

    warrant = {
        "liid": req.liid, "lea_id": req.lea_id,
        "target_id_type": target_type, "target_value": target_value,
        "target_msisdn": target_msisdn, "target_imsi": target_imsi, "target_imei": target_imei,
        "intercept_type": req.intercept_type,
        "legal_authority": req.legal_authority, "auth_ref": req.auth_ref,
        "delivery_ip": delivery_ip, "delivery_port": delivery_port,
        "delivery_address": delivery_addr,
        "valid_from": req.valid_from, "valid_until": req.valid_until,
        "country_code": req.country_code, "active": True,
    }
    with _lock:
        _warrants[req.liid] = warrant

    _x1_provision(warrant, "ACTIVATE")
    logger.info("HI1 ACTIVATE: LIID=%s MSISDN=%s IMSI=%s type=%s LEA=%s",
                req.liid, target_msisdn, target_imsi, req.intercept_type, req.lea_id)
    return {"liid": req.liid, "status": "ACTIVATED", "message": "Intercept activated successfully",
            "x1_provisioned": ["MME","SGW","PGW"] if req.intercept_type=="IRI_AND_CC" else
                              ["MME"] if req.intercept_type=="IRI_ONLY" else ["SGW","PGW"]}


@app.post("/hi1/warrants/deactivate", tags=["HI1"])
def deactivate_warrant(req: DeactivateReq):
    with db() as d:
        row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (req.liid,))
        if not row:
            raise HTTPException(404, f"Warrant {req.liid} not found")
        d.update("UPDATE warrants SET active=0 WHERE liid=?", (req.liid,))

    with _lock:
        if req.liid in _warrants:
            _warrants[req.liid]["active"] = False
        warrant = dict(row)
        warrant["liid"] = req.liid

    _x1_provision(warrant, "DEACTIVATE")
    logger.info("HI1 DEACTIVATE: LIID=%s", req.liid)
    return {"liid": req.liid, "status": "DEACTIVATED"}


@app.get("/hi1/warrants", tags=["HI1"])
def list_warrants():
    with db() as d:
        rows = d.fetchall(
            "SELECT * FROM warrants WHERE active=1 AND valid_until > ? ORDER BY created_at DESC",
            (datetime.utcnow().isoformat(),)
        )
    result = []
    for r in rows:
        result.append({
            "liid":           r["liid"],
            "lea_id":         r.get("lea_id",""),
            "target_msisdn":  r.get("target_msisdn") or r.get("target_value",""),
            "target_imsi":    r.get("target_imsi",""),
            "intercept_type": r.get("intercept_type","IRI_AND_CC"),
            "legal_authority":r.get("legal_authority",""),
            "auth_ref":       r.get("auth_ref",""),
            "delivery_ip":    r.get("delivery_ip",""),
            "delivery_port":  r.get("delivery_port",8443),
            "valid_from":     r.get("valid_from",""),
            "valid_until":    r.get("valid_until",""),
            "country_code":   r.get("country_code","IN"),
            "active":         True,
        })
    return result


@app.get("/hi1/warrants/{liid}", tags=["HI1"])
def get_warrant(liid: str):
    with db() as d:
        row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (liid,))
    if not row: raise HTTPException(404, f"Warrant {liid} not found")
    return row


# ── HI1 SOAP — ETSI TS 103 120 ───────────────────────────────

SOAP_ENV_NS = "http://schemas.xmlsoap.org/soap/envelope/"
HI1_NS      = "urn:etsi:103120:hi1:2019"


def _soap_ok(operation: str, liid: str, status: str, message: str = "") -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}" xmlns:hi1="{HI1_NS}">'
            f'<soapenv:Header/><soapenv:Body>'
            f'<hi1:{operation}Response>'
            f'<hi1:LIID>{liid}</hi1:LIID>'
            f'<hi1:Result>{status}</hi1:Result>'
            f'<hi1:Message>{message}</hi1:Message>'
            f'<hi1:Timestamp>{datetime.utcnow().isoformat()}Z</hi1:Timestamp>'
            f'</hi1:{operation}Response>'
            f'</soapenv:Body></soapenv:Envelope>')


def _soap_fault(code: str, reason: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}">'
            f'<soapenv:Body><soapenv:Fault>'
            f'<faultcode>{code}</faultcode><faultstring>{reason}</faultstring>'
            f'</soapenv:Fault></soapenv:Body></soapenv:Envelope>')


def _xml(el, tag: str) -> str:
    node = el.find(f"{{{HI1_NS}}}{tag}") or el.find(tag)
    return node.text.strip() if node is not None and node.text else ""


@app.post("/hi1/soap", tags=["HI1-SOAP"], response_class=Response,
          summary="HI1 SOAP — ETSI TS 103 120")
async def hi1_soap(request: Request):
    raw = await request.body()
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        return Response(_soap_fault("soapenv:Client", f"XML parse error: {e}"), media_type="text/xml", status_code=400)

    soap_body = root.find(f"{{{SOAP_ENV_NS}}}Body")
    if soap_body is None:
        return Response(_soap_fault("soapenv:Client", "Missing SOAP Body"), media_type="text/xml", status_code=400)

    # ── ActivateRequest or ActivateInterceptionRequest ────────
    req_el = (soap_body.find(f"{{{HI1_NS}}}ActivateRequest")
           or soap_body.find(f"{{{HI1_NS}}}ActivateInterceptionRequest")
           or soap_body.find("ActivateRequest")
           or soap_body.find("ActivateInterceptionRequest"))
    if req_el is not None:
        liid   = _xml(req_el, "LIID")
        lea_id = _xml(req_el, "LEAID")
        legal  = _xml(req_el, "LegalAuthority")
        auth_ref     = _xml(req_el, "AuthorizationReference")
        authorized_by= _xml(req_el, "AuthorizedBy")
        country_code = _xml(req_el, "AuthorizationCountryCode") or "IN"

        # InterceptionType
        itype = "IRI_AND_CC"
        it_el = req_el.find(f"{{{HI1_NS}}}InterceptionType") or req_el.find("InterceptionType")
        if it_el is not None:
            iri = (_xml(it_el,"IRICapture") or _xml(it_el,"IRI")).lower() == "true"
            cc  = (_xml(it_el,"CCCapture")  or _xml(it_el,"CC")).lower()  == "true"
            itype = ("IRI_AND_CC" if iri and cc else "IRI_ONLY" if iri else "CC_ONLY")

        # TargetIdentifiers
        msisdn=""; imsi=""; imei=""
        tgt = req_el.find(f"{{{HI1_NS}}}TargetIdentifiers") or req_el.find("TargetIdentifiers")
        if tgt is not None:
            msisdn = _xml(tgt,"MSISDN"); imsi = _xml(tgt,"IMSI"); imei = _xml(tgt,"IMEI")
            # also look inside TargetIdentifier child
            tid = tgt.find(f"{{{HI1_NS}}}TargetIdentifier") or tgt.find("TargetIdentifier")
            if tid is not None:
                msisdn = msisdn or _xml(tid,"MSISDN")
                imsi   = imsi   or _xml(tid,"IMSI")

        # DeliveryAddress
        delivery_ip=""; delivery_port=8443
        da_el = req_el.find(f"{{{HI1_NS}}}DeliveryAddress") or req_el.find("DeliveryAddress")
        if da_el is not None:
            delivery_ip   = _xml(da_el,"IPAddress")
            try: delivery_port = int(_xml(da_el,"Port")) if _xml(da_el,"Port") else 8443
            except: pass

        # Validity
        vp_el = req_el.find(f"{{{HI1_NS}}}ValidityPeriod") or req_el.find("ValidityPeriod")
        valid_from  = _xml(vp_el or req_el,"ValidFrom")  or datetime.utcnow().isoformat()
        valid_until = _xml(vp_el or req_el,"ValidUntil") or "2099-12-31T23:59:59Z"

        try:
            result = activate_warrant(ActivateReq(
                liid=liid, lea_id=lea_id,
                target_msisdn=msisdn, target_imsi=imsi, target_imei=imei,
                intercept_type=itype, legal_authority=legal, auth_ref=auth_ref,
                authorized_by=authorized_by, delivery_ip=delivery_ip,
                delivery_port=delivery_port, valid_from=valid_from,
                valid_until=valid_until, country_code=country_code,
            ))
            xml_out = _soap_ok("Activate", liid, "SUCCESS",
                               f"Intercept activated. IMSI={imsi} MSISDN={msisdn}")
            return Response(xml_out, media_type="text/xml")
        except HTTPException as e:
            return Response(_soap_fault("soapenv:Server", e.detail), media_type="text/xml", status_code=e.status_code)

    # ── DeactivateRequest ─────────────────────────────────────
    req_el = (soap_body.find(f"{{{HI1_NS}}}DeactivateRequest")
           or soap_body.find(f"{{{HI1_NS}}}DeactivateInterceptionRequest")
           or soap_body.find("DeactivateRequest"))
    if req_el is not None:
        liid   = _xml(req_el, "LIID")
        lea_id = _xml(req_el, "LEAID")
        try:
            deactivate_warrant(DeactivateReq(liid=liid, lea_id=lea_id))
            return Response(_soap_ok("Deactivate", liid, "SUCCESS", "Intercept deactivated"), media_type="text/xml")
        except HTTPException as e:
            return Response(_soap_fault("soapenv:Server", e.detail), media_type="text/xml", status_code=e.status_code)

    # ── GetInterceptionStatus ─────────────────────────────────
    req_el = (soap_body.find(f"{{{HI1_NS}}}GetInterceptionStatusRequest")
           or soap_body.find("GetInterceptionStatusRequest"))
    if req_el is not None:
        liid = _xml(req_el, "LIID")
        with db() as d:
            row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (liid,))
        if not row:
            return Response(_soap_fault("soapenv:Server", f"Warrant {liid} not found"), media_type="text/xml", status_code=404)
        status = "ACTIVE" if row.get("active") else "INACTIVE"
        xml_out = (f'<?xml version="1.0" encoding="UTF-8"?>'
                   f'<soapenv:Envelope xmlns:soapenv="{SOAP_ENV_NS}" xmlns:hi1="{HI1_NS}">'
                   f'<soapenv:Body><hi1:GetInterceptionStatusResponse>'
                   f'<hi1:LIID>{liid}</hi1:LIID>'
                   f'<hi1:Status>{status}</hi1:Status>'
                   f'<hi1:InterceptionType>{row.get("intercept_type","")}</hi1:InterceptionType>'
                   f'<hi1:ValidUntil>{row.get("valid_until","")}</hi1:ValidUntil>'
                   f'</hi1:GetInterceptionStatusResponse></soapenv:Body></soapenv:Envelope>')
        return Response(xml_out, media_type="text/xml")

    return Response(_soap_fault("soapenv:Client", "Unknown SOAP operation"), media_type="text/xml", status_code=400)


# ── X2 — IRI Events from NE (ASN.1 BER supported) ────────────

@app.post("/x2/iri", tags=["X2"])
def receive_iri(event: X2IriEvent):
    # Check warrant is active
    with _lock:
        active = _warrants.get(event.liid, {}).get("active", False)
    if not active:
        with db() as d:
            row = d.fetchone("SELECT active FROM warrants WHERE liid=?", (event.liid,))
        active = bool(row and row.get("active"))
        if active:
            with _lock:
                if event.liid not in _warrants:
                    _warrants[event.liid] = {}
                _warrants[event.liid]["active"] = True
    if not active:
        logger.warning("X2: LIID=%s not active — discarding", event.liid)
        raise HTTPException(400, f"LIID {event.liid} is not active")

    # Increment sequence
    with _lock:
        _seq[event.liid] = _seq.get(event.liid, 0) + 1
        seq = _seq[event.liid]

    ts  = event.timestamp or datetime.utcnow().isoformat()

    # Decode ASN.1 if present
    asn1_decoded = {}
    if event.asn1_hex:
        asn1_decoded = _ber_decode_iri(event.asn1_hex)
        logger.info("X2 ASN.1 decoded: %s", asn1_decoded)

    # Build unified payload from multiple sources
    payload_dict = {}
    if isinstance(event.payload, dict):
        payload_dict = event.payload
    # Merge legacy flat fields
    if event.imsi:    payload_dict["imsi"]    = event.imsi
    if event.msisdn:  payload_dict["msisdn"]  = event.msisdn
    if event.cell_id: payload_dict["cell_id"] = event.cell_id
    if event.tai:     payload_dict["tai"]      = event.tai
    if event.apn:     payload_dict["apn"]      = event.apn
    if event.ue_ip:   payload_dict["ue_ip"]    = event.ue_ip
    # Augment from ASN.1 decode
    if asn1_decoded:
        if "targetIMSI"   in asn1_decoded: payload_dict.setdefault("imsi",    asn1_decoded["targetIMSI"])
        if "targetMSISDN" in asn1_decoded: payload_dict.setdefault("msisdn",  asn1_decoded["targetMSISDN"])
        if "cellID"       in asn1_decoded: payload_dict.setdefault("cell_id", asn1_decoded["cellID"])
        if "tai"          in asn1_decoded: payload_dict.setdefault("tai",     asn1_decoded["tai"])
        if "ueIPAddress"  in asn1_decoded: payload_dict.setdefault("ue_ip",   asn1_decoded["ueIPAddress"])
        if "apn"          in asn1_decoded: payload_dict.setdefault("apn",     asn1_decoded["apn"])

    imsi = payload_dict.get("imsi", "")

    record = {
        "liid":       event.liid,
        "event_type": event.event_type,
        "ne_source":  event.ne_source or "MME",
        "ts":         ts,
        "seq_no":     seq,
        "payload":    payload_dict,
        "asn1_hex":   event.asn1_hex or "",
        "asn1_decoded": asn1_decoded,
        "encoding":   event.encoding or "JSON",
        "imsi":       imsi,
    }

    with _lock:
        _iri_events.append(record)

    with db() as d:
        d.insert("""INSERT INTO iri_log
            (liid,seq,event_type,ts,ne_source,imsi,payload,asn1_hex,encoding,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (event.liid, seq, event.event_type, ts,
             event.ne_source or "MME", imsi,
             json.dumps(payload_dict), event.asn1_hex or "",
             event.encoding or "JSON", datetime.utcnow().isoformat()))

    # HI2 push to LEA (async, non-blocking)
    _hi2_push_async(event.liid, record)

    logger.info("X2 IRI [%s]: LIID=%s seq=%d event=%s asn1=%s",
                event.ne_source or "MME", event.liid, seq, event.event_type,
                "yes" if event.asn1_hex else "no")
    return {"status": "accepted", "liid": event.liid, "seq": seq,
            "asn1_decoded": asn1_decoded if event.asn1_hex else None}


@app.get("/x2/iri/log", tags=["X2"])
def get_iri_log():
    with _lock:
        return list(reversed(_iri_events[-100:]))


@app.get("/x2/iri/log/db", tags=["X2"])
def get_iri_log_db(liid: Optional[str] = None, limit: int = 200):
    if liid:
        sql, params = "SELECT * FROM iri_log WHERE liid=? ORDER BY id DESC LIMIT ?", (liid, limit)
    else:
        sql, params = "SELECT * FROM iri_log ORDER BY id DESC LIMIT ?", (limit,)
    with db() as d:
        rows = d.fetchall(sql, params)
    # Parse JSON payload
    for r in rows:
        if r.get("payload") and isinstance(r["payload"], str):
            try: r["payload"] = json.loads(r["payload"])
            except: pass
    return rows


# ── X3 — CC Packets from NE (SFTP delivery to LEA) ───────────

@app.post("/x3/cc", tags=["X3"])
def receive_cc(pkt: X3CcPacket):
    with _lock:
        active = _warrants.get(pkt.liid, {}).get("active", False)
    if not active:
        raise HTTPException(400, f"LIID {pkt.liid} is not active")

    with _lock:
        _seq[pkt.liid] = _seq.get(pkt.liid, 0) + 1
        seq = _seq[pkt.liid]

    # Build payload
    payload_dict = {}
    if isinstance(pkt.payload, dict):
        payload_dict = pkt.payload
    payload_dict.setdefault("protocol", "GTP-U")
    payload_dict.setdefault("ne_source", pkt.ne_source or "SGW")
    if pkt.src_ip: payload_dict.setdefault("src_ip", pkt.src_ip)
    if pkt.dst_ip: payload_dict.setdefault("dst_ip", pkt.dst_ip)
    if pkt.src_port: payload_dict.setdefault("src_port", pkt.src_port)
    if pkt.dst_port: payload_dict.setdefault("dst_port", pkt.dst_port)
    payload_dict.setdefault("size_bytes", 512)

    # SFTP delivery
    sftp_ok, sftp_path = False, ""
    if pkt.deliver_sftp and _sftp_config.get("host"):
        sftp_ok, sftp_path = _deliver_sftp(pkt.liid, seq, payload_dict)

    record = {
        "liid":       pkt.liid,
        "ne_source":  pkt.ne_source or "SGW",
        "seq":        seq,
        "ts":         datetime.utcnow().isoformat(),
        "payload":    payload_dict,
        "sftp_delivered": sftp_ok,
        "sftp_path":  sftp_path,
    }

    with _lock:
        _cc_events.append(record)

    with db() as d:
        d.insert("""INSERT INTO cc_log
            (liid,seq,ne_source,direction,src_ip,dst_ip,payload,sftp_delivered,sftp_path,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (pkt.liid, seq, pkt.ne_source or "SGW",
             pkt.direction or "UPLINK",
             payload_dict.get("src_ip",""), payload_dict.get("dst_ip",""),
             json.dumps(payload_dict),
             1 if sftp_ok else 0, sftp_path,
             datetime.utcnow().isoformat()))

    logger.info("X3 CC [%s]: LIID=%s seq=%d sftp=%s", pkt.ne_source or "SGW", pkt.liid, seq, sftp_ok)
    return {"status": "accepted", "liid": pkt.liid, "seq": seq,
            "sftp_delivered": sftp_ok, "sftp_path": sftp_path}


@app.get("/x3/cc/log", tags=["X3"])
def get_cc_log():
    with _lock:
        return list(reversed(_cc_events[-100:]))


@app.get("/x3/cc/log/db", tags=["X3"])
def get_cc_log_db(liid: Optional[str] = None, limit: int = 200):
    if liid:
        sql, params = "SELECT * FROM cc_log WHERE liid=? ORDER BY id DESC LIMIT ?", (liid, limit)
    else:
        sql, params = "SELECT * FROM cc_log ORDER BY id DESC LIMIT ?", (limit,)
    with db() as d:
        rows = d.fetchall(sql, params)
    for r in rows:
        if r.get("payload") and isinstance(r["payload"], str):
            try: r["payload"] = json.loads(r["payload"])
            except: pass
    return rows


# ── SFTP config & test ────────────────────────────────────────

@app.post("/hi3/sftp/config", tags=["HI3"])
def set_sftp_config(cfg: SftpConfigReq):
    global _sftp_config
    _sftp_config = {"host": cfg.host, "port": cfg.port,
                    "username": cfg.username, "password": cfg.password}
    logger.info("SFTP config set: %s@%s:%s", cfg.username, cfg.host, cfg.port)
    return {"status": "ok", "host": cfg.host, "port": cfg.port}


@app.post("/hi3/sftp/test", tags=["HI3"])
def test_sftp(cfg: SftpConfigReq):
    """Test SFTP connectivity to the LEA machine."""
    try:
        import paramiko
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=cfg.host, port=cfg.port,
                       username=cfg.username, password=cfg.password, timeout=8)
        sftp = client.open_sftp()
        listing = sftp.listdir(".")
        sftp.close(); client.close()
        # Save config on success
        global _sftp_config
        _sftp_config = {"host": cfg.host, "port": cfg.port,
                        "username": cfg.username, "password": cfg.password}
        return {"ok": True, "host": cfg.host, "remote_files": listing[:10]}
    except ImportError:
        return JSONResponse({"ok": False, "error": "paramiko not installed. pip install paramiko --break-system-packages"}, status_code=500)
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/hi3/files", tags=["HI3"])
def list_cc_files():
    """List CC files received and tracked (both SFTP-delivered and local)."""
    # Also scan local cc_received dir
    _ensure_cc_dir()
    files = []
    seen = set()
    # From in-memory
    with _lock:
        for f in _cc_files:
            files.append(f); seen.add(f.get("name",""))
    # From disk
    try:
        for fname in sorted(os.listdir(CC_RECEIVED_DIR)):
            if fname not in seen:
                fp = os.path.join(CC_RECEIVED_DIR, fname)
                sz = os.path.getsize(fp)
                mtime = datetime.fromtimestamp(os.path.getmtime(fp)).isoformat()
                files.append({"name": fname, "size": f"{sz}B", "ts": mtime})
    except Exception:
        pass
    return {"files": sorted(files, key=lambda x: x.get("ts",""), reverse=True)[:100]}


# ── X1 Tasks ─────────────────────────────────────────────────

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


# ── Health ────────────────────────────────────────────────────

@app.get("/health", tags=["System"])
def health():
    db_type = "postgresql" if _is_pg() else "sqlite"
    uptime  = int(time.time() - _START_TIME)
    try:
        with db() as d:
            tw = (d.fetchone("SELECT COUNT(*) AS c FROM warrants") or {}).get("c", 0)
            ti = (d.fetchone("SELECT COUNT(*) AS c FROM iri_log")  or {}).get("c", 0)
            tc = (d.fetchone("SELECT COUNT(*) AS c FROM cc_log")   or {}).get("c", 0)
    except Exception:
        tw=ti=tc=0
    return {
        "status":           "ok",
        "version":          "v3.0 — India 4G LTE",
        "db":               db_type,
        "uptime_seconds":   uptime,
        "sftp_configured":  bool(_sftp_config.get("host")),
        "sftp_host":        _sftp_config.get("host",""),
        "active_warrants":  len([w for w in _warrants.values() if w.get("active")]),
        "iri_events_mem":   len(_iri_events),
        "cc_events_mem":    len(_cc_events),
        "total_warrants_db":tw,
        "total_iri_db":     ti,
        "total_cc_db":      tc,
        "x1_tasks":         {ne: len(t) for ne, t in _ne_tasks.items()},
    }


@app.get("/", include_in_schema=False)
def serve_portal():
    return FileResponse("portal/index.html")


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LIS Standalone Server — India 4G LTE")
    parser.add_argument("--port",     type=int, default=8001)
    parser.add_argument("--host",     default="0.0.0.0")
    parser.add_argument("--db-url",   default="",
        help="PostgreSQL URL: postgresql://lis:secret@localhost/lisdb  (default: SQLite)")
    parser.add_argument("--sftp-host",    default="", help="LEA SFTP host for HI3 CC delivery")
    parser.add_argument("--sftp-port",    type=int, default=2222)
    parser.add_argument("--sftp-user",    default="lea")
    parser.add_argument("--sftp-pass",    default="")
    args = parser.parse_args()

    DB_URL = args.db_url

    if args.sftp_host:
        _sftp_config.update({"host": args.sftp_host, "port": args.sftp_port,
                              "username": args.sftp_user, "password": args.sftp_pass})

    _ensure_cc_dir()
    db_init()
    _load_warrants_from_db()

    db_label   = f"PostgreSQL ({args.db_url.split('@')[-1]})" if _is_pg() else f"SQLite ({SQLITE_PATH})"
    sftp_label = f"SFTP → {args.sftp_host}:{args.sftp_port}" if args.sftp_host else "SFTP not configured (use /hi3/sftp/config)"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║      LIS Standalone Server v3.0 — India 4G LTE              ║
║      DoT / MHA Authorised Lawful Interception System         ║
╠══════════════════════════════════════════════════════════════╣
║  Portal:    http://localhost:{args.port:<5}                      ║
║  API Docs:  http://localhost:{args.port:<5}/docs                  ║
╠══════════════════════════════════════════════════════════════╣
║  Storage:   {db_label:<49}║
║  HI3 SFTP:  {sftp_label:<49}║
╠══════════════════════════════════════════════════════════════╣
║  HI1  SOAP  /hi1/soap             (ETSI TS 103 120)         ║
║  X2   POST  /x2/iri               (ASN.1 BER accepted)      ║
║  X3   POST  /x3/cc                (SFTP delivery to LEA)    ║
║  X1   GET   /x1/tasks/{{mme|sgw|pgw}} (NE provisioning)     ║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
