"""
LIS Standalone Server v3.1 — India 4G LTE Lawful Interception System
Zero Docker, Zero Kafka, Zero Redis. All in one process.

Interfaces:
  HI1  — SOAP/XML warrant activation (ETSI TS 103 120)
  HI2  — IRI delivery to LEA (ASN.1 BER, HTTPS push)
  HI3  — CC delivery to LEA via SFTP (paramiko)
  X1   — NE provisioning tasks (ADMF → MME/SGW/PGW)
  X2   — IRI events from NE (ASN.1 BER accepted)
  X3   — CC packets from NE (mirrored user-plane data)
  AUTH — Role-based login (SHA-256, Bearer token sessions)

Storage:
  SQLite (default):    python run_standalone.py
  PostgreSQL (Ubuntu): python run_standalone.py --db-url postgresql://lis:secret@localhost/lisdb

India context:
  Operators: Airtel 404-10, Jio 405-854, Vi 404-20, BSNL 404-07
  Legal: Telegraph Act S5(2), IT Act S69, UAPA S39, NIA Act S6
  Format: +91 MSISDN, 404/405 IMSI prefix
"""
import hashlib
import json
import os
import socket
import sqlite3
import struct
import threading
import uuid
import logging
import argparse
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from contextlib import contextmanager
from typing import Optional, Any, Dict

import uvicorn
from fastapi import FastAPI, HTTPException, Request, Response, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("LIS")


# ═══════════════════════════════════════════════════════════════
#  AUTHENTICATION  (SHA-256 passwords, Bearer token sessions)
# ═══════════════════════════════════════════════════════════════

# ── User accounts (SHA-256 hashed passwords) ─────────────────
#
#  Role: admin  → LIS Dashboard  (lis_adm!n_d0t)
#  Role: lea    → LEA Portal     (lea_0ff!c3r_1B)
#  Role: ne     → NE Simulator   (ne_3ng1n33r_4G)
#
_USERS: dict[str, dict] = {
    "lis_adm!n_d0t": {
        "password_hash": "20e766054cc7e5b3f2662c7277162b70036b350ec9711e6ff3ab7fc59f94ebe0",
        "role":   "admin",
        "name":   "LIS Administrator (DoT)",
        "access": "LIS Dashboard — Full Access",
    },
    "lea_0ff!c3r_1B": {
        "password_hash": "b4cc57acfe3bd63add2dfee95a54ad40ca96234326f2bebb4d7d7c129b2e1dd9",
        "role":   "lea",
        "name":   "LEA Officer — Intelligence Bureau",
        "access": "LEA Portal — HI1 / HI2 / HI3",
    },
    "ne_3ng1n33r_4G": {
        "password_hash": "c26633674ee7301fba8f7e06172fe904d5757a4883a0aef36749cd4d449904e2",
        "role":   "ne",
        "name":   "NE Engineer — Network Simulator",
        "access": "NE Simulator — X1 / X2 / X3",
    },
}

# token → {username, role, name, expires_at (epoch)}
_sessions: dict[str, dict] = {}
_SESSION_TTL_HOURS = 8
_auth_lock = threading.Lock()

# Failed login tracking (per IP)
_failed_logins: dict[str, list] = {}
_MAX_FAILS = 5
_LOCKOUT_SECS = 300   # 5 minutes


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _is_locked_out(ip: str) -> bool:
    now = time.time()
    with _auth_lock:
        attempts = [t for t in _failed_logins.get(ip, []) if now - t < _LOCKOUT_SECS]
        _failed_logins[ip] = attempts
        return len(attempts) >= _MAX_FAILS


def _record_fail(ip: str):
    now = time.time()
    with _auth_lock:
        _failed_logins.setdefault(ip, []).append(now)


def _create_session(username: str, user: dict) -> str:
    token = str(uuid.uuid4()) + "-" + str(uuid.uuid4())   # 72-char token
    expires = time.time() + _SESSION_TTL_HOURS * 3600
    with _auth_lock:
        _sessions[token] = {
            "username":   username,
            "role":       user["role"],
            "name":       user["name"],
            "access":     user["access"],
            "expires_at": expires,
        }
    logger.info("AUTH: login user=%s role=%s", username, user["role"])
    return token


def _get_session(token: str) -> dict | None:
    with _auth_lock:
        s = _sessions.get(token)
        if s and time.time() < s["expires_at"]:
            return s
        if s:
            del _sessions[token]   # expired
    return None


def _purge_expired():
    now = time.time()
    with _auth_lock:
        expired = [t for t, s in _sessions.items() if now >= s["expires_at"]]
        for t in expired:
            del _sessions[t]


def _token_from_request(request: Request) -> str | None:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    # Also accept from cookie or query param for SOAP/browser edge cases
    return request.query_params.get("token")


def require_auth(request: Request) -> dict:
    """FastAPI dependency — raises 401 if not authenticated."""
    token = _token_from_request(request)
    client_ip = request.client.host if request.client else "unknown"
    if not token:
        logger.warning("AUTH: missing token, path=%s client_ip=%s", request.url.path, client_ip)
        raise HTTPException(status_code=401, detail="Authentication required. Please log in.")
    session = _get_session(token)
    if not session:
        logger.warning("AUTH: invalid/expired token, path=%s client_ip=%s token_prefix=%s",
                        request.url.path, client_ip, token[:8])
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please log in again.")
    return session


# ── Auth endpoints ────────────────────────────────────────────

class LoginReq(BaseModel):
    username: str
    password: str


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
        logger.debug("HI3 SFTP: skipped for LIID=%s seq=%d — no SFTP host configured", liid, seq)
        return False, ""
    t0 = time.time()
    logger.info("HI3 SFTP: connecting to %s:%s user=%s for LIID=%s seq=%d",
                _sftp_config["host"], _sftp_config.get("port", 2222), _sftp_config.get("username","lea"), liid, seq)
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
        logger.info("HI3 SFTP: delivered LIID=%s seq=%d → %s:%s/%s size=%dB elapsed=%.2fs",
                    liid, seq, _sftp_config["host"], _sftp_config.get("port",2222), remote_path,
                    len(cc_data), time.time()-t0)
        return True, remote_path
    except ImportError:
        logger.warning("HI3 SFTP: paramiko not installed — delivery skipped for LIID=%s. "
                       "pip install paramiko --break-system-packages", liid)
        return False, "paramiko_not_installed"
    except Exception as e:
        logger.warning("HI3 SFTP: delivery FAILED for LIID=%s seq=%d host=%s:%s — %s (elapsed=%.2fs)",
                       liid, seq, _sftp_config.get("host"), _sftp_config.get("port",2222), e, time.time()-t0)
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
        logger.debug("HI2 push: skipped for LIID=%s — no delivery_ip on warrant", liid)
        return
    url = f"http://{delivery_ip}:{delivery_port}/hi2/iri"  # LEA receiver endpoint
    t0 = time.time()
    logger.info("HI2 push: sending LIID=%s seq=%s event=%s → %s",
                liid, iri_record.get("seq_no") or iri_record.get("seq"),
                iri_record.get("event_type"), url)
    try:
        import urllib.request
        data = json.dumps(iri_record).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=5)
        logger.info("HI2 push: delivered LIID=%s to %s size=%dB elapsed=%.2fs", liid, url, len(data), time.time()-t0)
    except Exception as e:
        logger.warning("HI2 push: FAILED for LIID=%s to %s — %s (elapsed=%.2fs)", liid, url, e, time.time()-t0)


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
    else:
        logger.warning("HI2 push: cannot deliver LIID=%s — warrant has no resolvable delivery address "
                       "(delivery_ip and delivery_address both empty)", liid)


# ═══════════════════════════════════════════════════════════════
#  FASTAPI APP
# ═══════════════════════════════════════════════════════════════

app = FastAPI(
    title="LIS Standalone — India 4G LTE",
    version="3.1.0",
    description="Lawful Interception System — DoT/MHA Authorised | HI1/HI2/HI3/X1/X2/X3 | Role-based Auth"
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


# ── Auth routes ───────────────────────────────────────────────

@app.post("/auth/login", tags=["Auth"])
async def login(req: LoginReq, request: Request):
    """Authenticate and receive a Bearer token (valid 8 hours)."""
    ip = request.client.host if request.client else "unknown"

    if _is_locked_out(ip):
        logger.warning("AUTH: lockout IP=%s user=%s", ip, req.username)
        raise HTTPException(429, "Too many failed attempts. Try again in 5 minutes.")

    user = _USERS.get(req.username)
    if not user or _sha256(req.password) != user["password_hash"]:
        _record_fail(ip)
        remaining = _MAX_FAILS - len(_failed_logins.get(ip, []))
        logger.warning("AUTH: failed login user=%s IP=%s", req.username, ip)
        raise HTTPException(401, f"Invalid username or password. {max(0,remaining)} attempt(s) remaining.")

    token = _create_session(req.username, user)
    _purge_expired()

    return {
        "token":    token,
        "username": req.username,
        "role":     user["role"],
        "name":     user["name"],
        "access":   user["access"],
        "expires_in_hours": _SESSION_TTL_HOURS,
    }


@app.post("/auth/logout", tags=["Auth"])
def logout(request: Request):
    """Invalidate the current session token."""
    token = _token_from_request(request)
    if token:
        with _auth_lock:
            sess = _sessions.pop(token, None)
        if sess:
            logger.info("AUTH: logout user=%s role=%s", sess.get("username"), sess.get("role"))
        else:
            logger.warning("AUTH: logout called with unknown/expired token_prefix=%s", token[:8])
    else:
        logger.warning("AUTH: logout called with no token")
    return {"status": "logged_out"}


@app.get("/auth/me", tags=["Auth"])
def whoami(session: dict = Depends(require_auth)):
    """Return info about the currently logged-in user."""
    return {
        "username": session["username"],
        "role":     session["role"],
        "name":     session["name"],
        "access":   session["access"],
    }


@app.get("/auth/sessions", tags=["Auth"])
def list_sessions(session: dict = Depends(require_auth)):
    """Admin only — list active sessions."""
    if session["role"] != "admin":
        logger.warning("AUTH: non-admin user=%s (role=%s) denied access to /auth/sessions",
                        session.get("username"), session.get("role"))
        raise HTTPException(403, "Admin role required")
    _purge_expired()
    logger.info("AUTH: user=%s listed active sessions (count=%d)", session.get("username"), len(_sessions))
    with _auth_lock:
        return [
            {"username": s["username"], "role": s["role"],
             "expires_in_min": int((s["expires_at"] - time.time()) / 60)}
            for s in _sessions.values()
        ]


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
        logger.info("X1 [%s] %s → LIID=%s task_id=%s target_msisdn=%s target_imsi=%s "
                    "intercept_type=%s delivery=%s:%s queue_depth=%d",
                    ne, action, warrant["liid"], task["task_id"], task["target_msisdn"],
                    task["target_imsi"], itype, task["delivery_ip"], task["delivery_port"],
                    len(_ne_tasks[ne]))


@app.post("/hi1/warrants/activate", tags=["HI1"])
def activate_warrant(req: ActivateReq, session: dict = Depends(require_auth)):
    logger.info("HI1 ACTIVATE request: LIID=%s LEA=%s by user=%s raw=%s",
                req.liid, req.lea_id, session.get("username"), req.model_dump())
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
    logger.info("HI1 ACTIVATE OK: LIID=%s MSISDN=%s IMSI=%s IMEI=%s type=%s LEA=%s "
                "legal=%s auth_ref=%s authorized_by=%s delivery=%s:%s valid=%s..%s db_row=%s",
                req.liid, target_msisdn, target_imsi, target_imei, req.intercept_type, req.lea_id,
                req.legal_authority, req.auth_ref, req.authorized_by, delivery_ip, delivery_port,
                req.valid_from, req.valid_until, "updated" if existing else "inserted")
    return {"liid": req.liid, "status": "ACTIVATED", "message": "Intercept activated successfully",
            "x1_provisioned": ["MME","SGW","PGW"] if req.intercept_type=="IRI_AND_CC" else
                              ["MME"] if req.intercept_type=="IRI_ONLY" else ["SGW","PGW"]}


@app.post("/hi1/warrants/deactivate", tags=["HI1"])
def deactivate_warrant(req: DeactivateReq, session: dict = Depends(require_auth)):
    logger.info("HI1 DEACTIVATE request: LIID=%s LEA=%s by user=%s", req.liid, req.lea_id, session.get("username"))
    with db() as d:
        row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (req.liid,))
        if not row:
            logger.warning("HI1 DEACTIVATE FAILED: LIID=%s not found", req.liid)
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
def list_warrants(session: dict = Depends(require_auth)):
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
def get_warrant(liid: str, session: dict = Depends(require_auth)):
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


def _find(el, *tags):
    """Find first matching child by tag, trying namespaced then bare names.
    NOTE: must use explicit `is not None` checks — ElementTree Elements are
    falsy when they have no children, so `a.find(x) or a.find(y)` silently
    discards valid leaf-text elements (e.g. <hi1:LIID>VALUE</hi1:LIID>)."""
    if el is None:
        return None
    for tag in tags:
        node = el.find(f"{{{HI1_NS}}}{tag}")
        if node is not None:
            return node
        node = el.find(tag)
        if node is not None:
            return node
    return None


def _xml(el, tag: str) -> str:
    node = _find(el, tag)
    return node.text.strip() if node is not None and node.text else ""


@app.post("/hi1/soap", tags=["HI1-SOAP"], response_class=Response,
          summary="HI1 SOAP — ETSI TS 103 120")
async def hi1_soap(request: Request):
    client_ip = request.client.host if request.client else "unknown"
    # SOAP requests carry the token via Authorization header OR X-Auth-Token header
    token = None
    auth_hdr = request.headers.get("Authorization", "")
    if auth_hdr.startswith("Bearer "):
        token = auth_hdr[7:]
    if not token:
        token = request.headers.get("X-Auth-Token", "")
    if not token:
        logger.warning("HI1 SOAP: rejected — no auth token, IP=%s", client_ip)
        return Response(_soap_fault("soapenv:Client", "Authentication required. Include Authorization: Bearer <token> header."),
                        media_type="text/xml", status_code=401)
    sess = _get_session(token)
    if not sess:
        logger.warning("HI1 SOAP: rejected — invalid/expired token, IP=%s", client_ip)
        return Response(_soap_fault("soapenv:Client", "Invalid or expired session token."),
                        media_type="text/xml", status_code=401)
    _purge_expired()

    raw = await request.body()
    logger.info("HI1 SOAP: request received from IP=%s user=%s bytes=%d", client_ip, sess.get("username"), len(raw))
    logger.debug("HI1 SOAP: raw request body:\n%s", raw.decode("utf-8", errors="replace"))
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as e:
        logger.warning("HI1 SOAP: XML parse error from IP=%s: %s — raw(first 500B)=%r",
                       client_ip, e, raw[:500])
        return Response(_soap_fault("soapenv:Client", f"XML parse error: {e}"), media_type="text/xml", status_code=400)

    soap_body = root.find(f"{{{SOAP_ENV_NS}}}Body")
    if soap_body is None:
        logger.warning("HI1 SOAP: missing SOAP Body from IP=%s", client_ip)
        return Response(_soap_fault("soapenv:Client", "Missing SOAP Body"), media_type="text/xml", status_code=400)

    # ── ActivateRequest or ActivateInterceptionRequest ────────
    req_el = _find(soap_body, "ActivateRequest", "ActivateInterceptionRequest")
    if req_el is not None:
        liid   = _xml(req_el, "LIID")
        lea_id = _xml(req_el, "LEAID")
        legal  = _xml(req_el, "LegalAuthority")
        auth_ref     = _xml(req_el, "AuthorizationReference")
        authorized_by= _xml(req_el, "AuthorizedBy")
        country_code = _xml(req_el, "AuthorizationCountryCode") or "IN"

        # InterceptionType
        itype = "IRI_AND_CC"
        it_el = _find(req_el, "InterceptionType")
        if it_el is not None:
            iri = (_xml(it_el,"IRICapture") or _xml(it_el,"IRI")).lower() == "true"
            cc  = (_xml(it_el,"CCCapture")  or _xml(it_el,"CC")).lower()  == "true"
            itype = ("IRI_AND_CC" if iri and cc else "IRI_ONLY" if iri else "CC_ONLY")

        # TargetIdentifiers
        msisdn=""; imsi=""; imei=""
        tgt = _find(req_el, "TargetIdentifiers")
        if tgt is not None:
            msisdn = _xml(tgt,"MSISDN"); imsi = _xml(tgt,"IMSI"); imei = _xml(tgt,"IMEI")
            # also look inside TargetIdentifier child
            tid = _find(tgt, "TargetIdentifier")
            if tid is not None:
                msisdn = msisdn or _xml(tid,"MSISDN")
                imsi   = imsi   or _xml(tid,"IMSI")

        # DeliveryAddress
        delivery_ip=""; delivery_port=8443
        da_el = _find(req_el, "DeliveryAddress")
        if da_el is not None:
            delivery_ip   = _xml(da_el,"IPAddress")
            try: delivery_port = int(_xml(da_el,"Port")) if _xml(da_el,"Port") else 8443
            except: pass

        # Validity
        vp_el = _find(req_el, "ValidityPeriod")
        validity_src = vp_el if vp_el is not None else req_el
        valid_from  = _xml(validity_src,"ValidFrom")  or datetime.utcnow().isoformat()
        valid_until = _xml(validity_src,"ValidUntil") or "2099-12-31T23:59:59Z"

        logger.info("HI1 SOAP ActivateRequest parsed: LIID=%s LEA=%s legal=%s auth_ref=%s "
                    "authorized_by=%s country=%s itype=%s MSISDN=%s IMSI=%s IMEI=%s "
                    "delivery=%s:%s valid=%s..%s",
                    liid, lea_id, legal, auth_ref, authorized_by, country_code, itype,
                    msisdn, imsi, imei, delivery_ip, delivery_port, valid_from, valid_until)

        try:
            result = activate_warrant(ActivateReq(
                liid=liid, lea_id=lea_id,
                target_msisdn=msisdn, target_imsi=imsi, target_imei=imei,
                intercept_type=itype, legal_authority=legal, auth_ref=auth_ref,
                authorized_by=authorized_by, delivery_ip=delivery_ip,
                delivery_port=delivery_port, valid_from=valid_from,
                valid_until=valid_until, country_code=country_code,
            ), session=sess)
            xml_out = _soap_ok("Activate", liid, "SUCCESS",
                               f"Intercept activated. IMSI={imsi} MSISDN={msisdn}")
            logger.info("HI1 SOAP: ActivateResponse SUCCESS LIID=%s", liid)
            logger.debug("HI1 SOAP: response body:\n%s", xml_out)
            return Response(xml_out, media_type="text/xml")
        except HTTPException as e:
            logger.warning("HI1 SOAP: ActivateRequest FAILED LIID=%s status=%s detail=%s", liid, e.status_code, e.detail)
            return Response(_soap_fault("soapenv:Server", e.detail), media_type="text/xml", status_code=e.status_code)

    # ── DeactivateRequest ─────────────────────────────────────
    req_el = _find(soap_body, "DeactivateRequest", "DeactivateInterceptionRequest")
    if req_el is not None:
        liid   = _xml(req_el, "LIID")
        lea_id = _xml(req_el, "LEAID")
        logger.info("HI1 SOAP DeactivateRequest parsed: LIID=%s LEA=%s", liid, lea_id)
        try:
            deactivate_warrant(DeactivateReq(liid=liid, lea_id=lea_id), session=sess)
            logger.info("HI1 SOAP: DeactivateResponse SUCCESS LIID=%s", liid)
            return Response(_soap_ok("Deactivate", liid, "SUCCESS", "Intercept deactivated"), media_type="text/xml")
        except HTTPException as e:
            logger.warning("HI1 SOAP: DeactivateRequest FAILED LIID=%s status=%s detail=%s", liid, e.status_code, e.detail)
            return Response(_soap_fault("soapenv:Server", e.detail), media_type="text/xml", status_code=e.status_code)

    # ── GetInterceptionStatus ─────────────────────────────────
    req_el = _find(soap_body, "GetInterceptionStatusRequest")
    if req_el is not None:
        liid = _xml(req_el, "LIID")
        logger.info("HI1 SOAP GetInterceptionStatusRequest: LIID=%s", liid)
        with db() as d:
            row = d.fetchone("SELECT * FROM warrants WHERE liid=?", (liid,))
        if not row:
            logger.warning("HI1 SOAP: GetInterceptionStatus LIID=%s not found", liid)
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
        logger.info("HI1 SOAP: GetInterceptionStatusResponse LIID=%s status=%s", liid, status)
        return Response(xml_out, media_type="text/xml")

    logger.warning("HI1 SOAP: unknown/unsupported operation from IP=%s — body root tags=%s",
                   client_ip, [c.tag for c in soap_body])
    return Response(_soap_fault("soapenv:Client", "Unknown SOAP operation"), media_type="text/xml", status_code=400)


# ── X2 — IRI Events from NE (ASN.1 BER supported) ────────────

@app.post("/x2/iri", tags=["X2"])
def receive_iri(event: X2IriEvent, session: dict = Depends(require_auth)):
    logger.info("X2 IRI request received: LIID=%s ne_source=%s event_type=%s encoding=%s asn1=%s user=%s",
                event.liid, event.ne_source, event.event_type, event.encoding,
                "yes" if event.asn1_hex else "no", session.get("username"))
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
        logger.warning("X2: LIID=%s not active — discarding event_type=%s from ne_source=%s",
                       event.liid, event.event_type, event.ne_source)
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

    logger.info("X2 IRI [%s]: LIID=%s seq=%d event=%s asn1=%s payload=%s db_inserted=yes hi2_push=triggered",
                event.ne_source or "MME", event.liid, seq, event.event_type,
                "yes" if event.asn1_hex else "no", payload_dict)
    return {"status": "accepted", "liid": event.liid, "seq": seq,
            "asn1_decoded": asn1_decoded if event.asn1_hex else None}


@app.get("/x2/iri/log", tags=["X2"])
def get_iri_log(session: dict = Depends(require_auth)):
    with _lock:
        return list(reversed(_iri_events[-100:]))


@app.get("/x2/iri/log/db", tags=["X2"])
def get_iri_log_db(liid: Optional[str] = None, limit: int = 200, session: dict = Depends(require_auth)):
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
def receive_cc(pkt: X3CcPacket, session: dict = Depends(require_auth)):
    logger.info("X3 CC request received: LIID=%s ne_source=%s direction=%s deliver_sftp=%s user=%s",
                pkt.liid, pkt.ne_source, pkt.direction, pkt.deliver_sftp, session.get("username"))
    with _lock:
        active = _warrants.get(pkt.liid, {}).get("active", False)
    if not active:
        logger.warning("X3: LIID=%s not active — discarding CC packet from ne_source=%s", pkt.liid, pkt.ne_source)
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

    logger.info("X3 CC [%s]: LIID=%s seq=%d sftp_delivered=%s sftp_path=%s payload=%s",
                pkt.ne_source or "SGW", pkt.liid, seq, sftp_ok, sftp_path or "n/a", payload_dict)
    return {"status": "accepted", "liid": pkt.liid, "seq": seq,
            "sftp_delivered": sftp_ok, "sftp_path": sftp_path}


@app.get("/x3/cc/log", tags=["X3"])
def get_cc_log(session: dict = Depends(require_auth)):
    with _lock:
        return list(reversed(_cc_events[-100:]))


@app.get("/x3/cc/log/db", tags=["X3"])
def get_cc_log_db(liid: Optional[str] = None, limit: int = 200, session: dict = Depends(require_auth)):
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
def set_sftp_config(cfg: SftpConfigReq, session: dict = Depends(require_auth)):
    global _sftp_config
    _sftp_config = {"host": cfg.host, "port": cfg.port,
                    "username": cfg.username, "password": cfg.password}
    logger.info("HI3 SFTP config set by user=%s: %s@%s:%s (password redacted)",
                session.get("username"), cfg.username, cfg.host, cfg.port)
    return {"status": "ok", "host": cfg.host, "port": cfg.port}


@app.post("/hi3/sftp/test", tags=["HI3"])
def test_sftp(cfg: SftpConfigReq, session: dict = Depends(require_auth)):
    """Test SFTP connectivity to the LEA machine."""
    logger.info("HI3 SFTP test requested by user=%s: %s@%s:%s",
                session.get("username"), cfg.username, cfg.host, cfg.port)
    t0 = time.time()
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
        logger.info("HI3 SFTP test OK: %s:%s remote_file_count=%d elapsed=%.2fs",
                    cfg.host, cfg.port, len(listing), time.time() - t0)
        return {"ok": True, "host": cfg.host, "remote_files": listing[:10]}
    except ImportError:
        logger.warning("HI3 SFTP test FAILED: paramiko not installed")
        return JSONResponse({"ok": False, "error": "paramiko not installed. pip install paramiko --break-system-packages"}, status_code=500)
    except Exception as e:
        logger.warning("HI3 SFTP test FAILED: %s:%s error=%s elapsed=%.2fs",
                       cfg.host, cfg.port, e, time.time() - t0)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


@app.get("/hi3/files", tags=["HI3"])
def list_cc_files(session: dict = Depends(require_auth)):
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
    except Exception as e:
        logger.warning("HI3 list_cc_files: error scanning %s: %s", CC_RECEIVED_DIR, e)
    logger.debug("HI3 list_cc_files: user=%s returned %d file(s)", session.get("username"), len(files))
    return {"files": sorted(files, key=lambda x: x.get("ts",""), reverse=True)[:100]}


# ── X1 Tasks ─────────────────────────────────────────────────

@app.get("/x1/tasks/{ne}", tags=["X1"])
def get_ne_tasks(ne: str, session: dict = Depends(require_auth)):
    ne = ne.upper()
    if ne not in _ne_tasks:
        logger.warning("X1: get_ne_tasks unknown NE=%s requested by user=%s", ne, session.get("username"))
        raise HTTPException(404, f"Unknown NE: {ne}")
    with _lock:
        tasks = list(reversed(_ne_tasks[ne]))
    logger.debug("X1: NE=%s polled tasks by user=%s, queue_depth=%d", ne, session.get("username"), len(tasks))
    return tasks


@app.delete("/x1/tasks/{ne}", tags=["X1"])
def clear_ne_tasks(ne: str, session: dict = Depends(require_auth)):
    ne = ne.upper()
    with _lock:
        cleared_count = len(_ne_tasks.get(ne, []))
        _ne_tasks[ne] = []
    logger.info("X1: NE=%s task queue cleared (%d task(s) discarded) by user=%s",
                ne, cleared_count, session.get("username"))
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
#  X2 TCP SERVER  (ASN.1 BER IRI — 3GPP TS 33.108 Annex B.9)
# ═══════════════════════════════════════════════════════════════
#
# Protocol: each message is framed as:
#   [4 bytes big-endian length][ASN.1 BER payload]
# Compatible with real PGW/GGSN X2 implementations.

def _run_x2_tcp_server(port: int):
    """
    TCP listener for X2 IRI messages from NE.
    Framing: TPKT per RFC 1006 / RFC 2126 (ISO Transport on TCP).
    Encoding: ASN.1 BER per TS 33.108 Annex B.9.
    Also accepts legacy 4-byte raw length prefix (auto-detected).
    """
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("0.0.0.0", port))
        srv.listen(10)
        logger.info("X2 TCP server 0.0.0.0:%d (TPKT/RFC1006 + ASN.1 BER)", port)
        while True:
            try:
                conn, addr = srv.accept()
                logger.info("X2 TCP: NE connected from %s:%d", *addr)
                t = threading.Thread(target=_handle_x2_client, args=(conn, addr), daemon=True)
                t.start()
            except Exception as e:
                logger.error("X2 TCP accept error: %s", e)
                time.sleep(1)
    except Exception as e:
        logger.error("X2 TCP server failed to start on port %d: %s", port, e)


def _handle_x2_client(conn: socket.socket, addr):
    """
    Handle a single NE X2 TCP connection.
    Parses TPKT frames (RFC 1006, version byte = 0x03) to extract ASN.1 BER IRI.
    Falls back to raw 4-byte big-endian length prefix for legacy senders.
    """
    ne_addr = f"{addr[0]}:{addr[1]}"
    buf = b""
    TPKT_VER = 0x03
    try:
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                logger.info("X2 TCP: NE %s disconnected", ne_addr)
                break
            buf += chunk
            # Drain all complete frames from buffer
            while len(buf) >= 4:
                first_byte = buf[0]
                if first_byte == TPKT_VER:
                    # ── TPKT frame (RFC 1006/2126) ──
                    # Header: version(1) reserved(1) total_length(2)  — total_length includes header
                    _ver, _res, total_len = struct.unpack(">BBH", buf[:4])
                    if total_len < 4:
                        buf = buf[1:]   # bad frame, skip
                        continue
                    if len(buf) < total_len:
                        break           # wait for rest
                    asn1_bytes = buf[4:total_len]
                    buf        = buf[total_len:]
                else:
                    # ── Legacy: raw 4-byte big-endian length prefix ──
                    msg_len = struct.unpack(">I", buf[:4])[0]
                    if msg_len > 65536:
                        buf = buf[1:]   # sanity: skip bad byte
                        continue
                    if len(buf) < 4 + msg_len:
                        break
                    asn1_bytes = buf[4:4 + msg_len]
                    buf        = buf[4 + msg_len:]

                try:
                    _process_x2_tcp_iri(asn1_bytes, ne_addr)
                except Exception as e:
                    logger.warning("X2 TCP: IRI decode error from %s: %s", ne_addr, e)
    except Exception as e:
        logger.warning("X2 TCP client %s error: %s", ne_addr, e)
    finally:
        try: conn.close()
        except: pass


def _process_x2_tcp_iri(asn1_bytes: bytes, ne_addr: str):
    """Decode ASN.1 BER IRI received over TCP and store/deliver same as HTTP X2."""
    asn1_hex = asn1_bytes.hex()
    decoded  = _ber_decode_iri(asn1_hex)
    ts       = datetime.utcnow().isoformat()
    liid     = decoded.get("liid", "UNKNOWN")
    event    = decoded.get("event_type", "UNKNOWN")
    seq      = decoded.get("seq", 0)

    payload = {
        "liid":     liid,
        "event_type": event,
        "imsi":     decoded.get("imsi", ""),
        "msisdn":   decoded.get("msisdn", ""),
        "cell_id":  decoded.get("cell_id", ""),
        "ue_ip":    decoded.get("ue_ip", ""),
        "apn":      decoded.get("apn", ""),
        "ts":       ts,
        "transport": "X2_TCP_ASN1_BER",
        "ne_source": ne_addr,
    }

    with _lock:
        _iri_events.append(payload)
    logger.info("X2 TCP: IRI event liid=%s type=%s from=%s seq=%d", liid, event, ne_addr, seq)

    with db() as d:
        d.execute(
            "INSERT INTO iri_log(liid,event_type,ts,asn1_hex,ne_source,imsi,payload,encoding)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (liid, event, ts, asn1_hex, ne_addr,
             decoded.get("imsi",""), json.dumps(payload), "ASN1_BER_TCP")
        )

    # Push HI2 to LEA asynchronously (same path as HTTP X2)
    _hi2_push_async(liid, payload)


# ═══════════════════════════════════════════════════════════════
#  X3 UDP SERVER  (ULIC header — 3GPP TS 33.108 Annex C)
# ═══════════════════════════════════════════════════════════════
#
# Supports ULICv08 (version byte = 0x08) and ULICv1 (version byte = 0x01)
#
# ULICv08 frame layout (big-endian):
#   Offset  Size  Field
#      0     1    Version (0x08)
#      1     1    Header Length (bytes, including version & hdr_len fields)
#      2     2    PDU Length (payload only)
#      4     4    Sequence Number
#      8     4    Timestamp (UTC seconds since epoch)
#     12     1    LI-ID Length (N)
#     13     N    LI-ID (ASCII)
#   13+N     1    Direction (0=from-target/uplink, 1=to-target/downlink)
#   14+N     1    Content Type (1=IPv4, 2=IPv6, 3=Ethernet)
#   15+N     ...  CC Payload
#
# ULICv1 frame layout:
#   Offset  Size  Field
#      0     1    Version (0x01)
#      1     1    Header Length
#      2     2    PDU Length
#      4     4    Sequence Number
#      8     4    Timestamp
#     12     2    LI-ID Length (N)
#     14     N    LI-ID
#   14+N     1    Direction
#   15+N     1    Content Type
#   16+N     ...  CC Payload

ULIC_VERSION_V0  = 0x00   # TS 33.108 §C.1.2 — UDP standard
ULIC_VERSION_V08 = 0x08   # Nokia/ALU vendor — UDP extended
ULIC_VERSION_V1  = 0x01   # TS 33.108 §C.1.3 — TCP
ULIC_VERSION_V09 = 0x09   # TS 33.108 ULICv09
ULIC_DIRECTION   = {0: "UPLINK (from target)", 1: "DOWNLINK (to target)"}
ULIC_CONTENT     = {1: "IPv4", 2: "IPv6", 3: "Ethernet", 4: "TCP", 5: "UDP"}

# ULIC versions that use a 1-byte liid_len field
_ULIC_1BYTE_LIID = {ULIC_VERSION_V0, ULIC_VERSION_V08}
# ULIC versions that use a 2-byte liid_len field
_ULIC_2BYTE_LIID = {ULIC_VERSION_V1, ULIC_VERSION_V09}

_ULIC_VER_NAMES = {
    ULIC_VERSION_V0:  "ULICv0",
    ULIC_VERSION_V08: "ULICv08",
    ULIC_VERSION_V1:  "ULICv1",
    ULIC_VERSION_V09: "ULICv09",
}


def _parse_ulic_header(data: bytes) -> dict:
    """
    Parse ULIC header (any version).
    Versions v0/v08: 1-byte liid_len (TS 33.108 §C.1.2, Nokia ext)
    Versions v1/v09: 2-byte liid_len (TS 33.108 §C.1.3, v09)
    Returns dict with header fields + payload_off.
    """
    if len(data) < 13:
        raise ValueError(f"ULIC packet too short: {len(data)} bytes")

    version   = data[0]
    hdr_len   = data[1]
    pdu_len   = struct.unpack(">H", data[2:4])[0]
    seq_num   = struct.unpack(">I", data[4:8])[0]
    timestamp = struct.unpack(">I", data[8:12])[0]

    if version in _ULIC_1BYTE_LIID:
        liid_len   = data[12]
        liid_start = 13
    elif version in _ULIC_2BYTE_LIID:
        if len(data) < 14:
            raise ValueError("ULIC v1/v09 packet too short for 2-byte liid_len")
        liid_len   = struct.unpack(">H", data[12:14])[0]
        liid_start = 14
    else:
        # Unknown version: try 1-byte liid_len as best-effort
        liid_len   = data[12]
        liid_start = 13
        logger.debug("ULIC: unknown version 0x%02x — trying 1-byte liid_len", version)

    liid_end     = liid_start + liid_len
    liid         = data[liid_start:liid_end].decode("ascii", errors="replace")
    direction    = data[liid_end]     if liid_end     < len(data) else 0
    content_type = data[liid_end + 1] if liid_end + 1 < len(data) else 0
    payload_off  = liid_end + 2

    return {
        "version":      _ULIC_VER_NAMES.get(version, f"ULICv0x{version:02x}"),
        "version_byte": version,
        "hdr_len":      hdr_len,
        "pdu_len":      pdu_len,
        "seq_num":      seq_num,
        "timestamp":    timestamp,
        "liid":         liid,
        "direction":    ULIC_DIRECTION.get(direction, f"0x{direction:02x}"),
        "content_type": ULIC_CONTENT.get(content_type, f"0x{content_type:02x}"),
        "payload_off":  payload_off,
    }


def _run_x3_udp_server(port: int):
    """UDP listener for X3 CC content from NE with ULIC header (TS 33.108 Annex C)."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", port))
        logger.info("X3 UDP server 0.0.0.0:%d (ULICv0/v08/v09/v1 per TS 33.108 Annex C)", port)
        while True:
            try:
                data, addr = sock.recvfrom(65535)
                threading.Thread(
                    target=_process_x3_ulic_packet,
                    args=(data, addr), daemon=True
                ).start()
            except Exception as e:
                logger.error("X3 UDP recv error: %s", e)
    except Exception as e:
        logger.error("X3 UDP server failed to start on port %d: %s", port, e)


def _process_x3_ulic_packet(data: bytes, addr):
    """Parse ULIC header and store CC content, then deliver via SFTP."""
    ne_addr = f"{addr[0]}:{addr[1]}"
    try:
        hdr     = _parse_ulic_header(data)
        payload = data[hdr["payload_off"]:]
        ts      = datetime.utcnow().isoformat()
        liid    = hdr["liid"] or "UNKNOWN"
        seq     = hdr["seq_num"]

        cc_info = {
            "liid":         liid,
            "ne_source":    ne_addr,
            "ulic_version": hdr["version"],
            "direction":    hdr["direction"],
            "content_type": hdr["content_type"],
            "seq":          seq,
            "ts":           ts,
            "size_bytes":   len(payload),
            "protocol":     hdr["content_type"],
            "transport":    "X3_UDP_ULIC",
        }

        logger.info("X3 UDP ULIC: liid=%s %s seq=%d size=%dB from=%s",
                    liid, hdr["version"], seq, len(payload), ne_addr)

        with _lock:
            _cc_events.append(cc_info)

        # Store in DB
        with db() as d:
            d.execute(
                "INSERT INTO cc_log(liid,ts,ne_source,payload,sftp_delivered)"
                " VALUES(?,?,?,?,0)",
                (liid, ts, ne_addr, json.dumps(cc_info))
            )

        # SFTP delivery to LEA
        if _sftp_config.get("host"):
            _deliver_sftp(liid, seq, payload)
            with db() as d:
                d.execute(
                    "UPDATE cc_log SET sftp_delivered=1 WHERE liid=? AND ts=?",
                    (liid, ts)
                )
        else:
            logger.debug("X3 UDP: liid=%s seq=%d stored only — no SFTP host configured", liid, seq)

    except Exception as e:
        logger.warning("X3 UDP: failed to parse ULIC from %s: %s — raw hex: %s",
                       ne_addr, e, data[:32].hex())


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LIS Standalone Server — India 4G LTE")
    parser.add_argument("--port",         type=int, default=8001,  help="HTTP API port")
    parser.add_argument("--host",         default="0.0.0.0")
    parser.add_argument("--db-url",       default="",
        help="PostgreSQL URL: postgresql://lis:secret@localhost/lisdb  (default: SQLite)")
    parser.add_argument("--sftp-host",    default="", help="LEA SFTP host for HI3 CC delivery")
    parser.add_argument("--sftp-port",    type=int, default=2222)
    parser.add_argument("--sftp-user",    default="lea")
    parser.add_argument("--sftp-pass",    default="")
    parser.add_argument("--x2-port",      type=int, default=4000,
        help="X2 TCP port for ASN.1 BER IRI from NE (TS 33.108 Annex B.9)")
    parser.add_argument("--x3-port",      type=int, default=4001,
        help="X3 UDP port for ULIC CC from NE (TS 33.108 Annex C)")
    args = parser.parse_args()

    DB_URL = args.db_url

    if args.sftp_host:
        _sftp_config.update({"host": args.sftp_host, "port": args.sftp_port,
                              "username": args.sftp_user, "password": args.sftp_pass})

    _ensure_cc_dir()
    db_init()
    _load_warrants_from_db()

    # Start X2 TCP and X3 UDP servers in background threads
    threading.Thread(target=_run_x2_tcp_server, args=(args.x2_port,),
                     daemon=True, name="X2-TCP").start()
    threading.Thread(target=_run_x3_udp_server, args=(args.x3_port,),
                     daemon=True, name="X3-UDP").start()

    db_label   = f"PostgreSQL ({args.db_url.split('@')[-1]})" if _is_pg() else f"SQLite ({SQLITE_PATH})"
    sftp_label = f"SFTP → {args.sftp_host}:{args.sftp_port}" if args.sftp_host else "SFTP not configured"

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║      LIS Standalone Server v3.1 — India 4G LTE              ║
║      DoT / MHA Authorised Lawful Interception System         ║
╠══════════════════════════════════════════════════════════════╣
║  HI1  SOAP   HTTP  :{args.port:<5}  /hi1/soap  (ETSI TS 103 120)║
║  HI2  PUSH   HTTP  → LEA:{args.sftp_port:<5} (IRI delivery)       ║
║  HI3  SFTP   {sftp_label:<47}║
╠══════════════════════════════════════════════════════════════╣
║  X1   HTTP   GET   :{args.port:<5}  /x1/tasks/{{mme|sgw|pgw}}  ║
║  X2   TCP    BER   :{args.x2_port:<5}  ASN.1 IRI (TS 33.108 B.9)║
║  X3   UDP    ULIC  :{args.x3_port:<5}  CC content (TS 33.108 C) ║
╠══════════════════════════════════════════════════════════════╣
║  Portal:    http://localhost:{args.port}                     ║
║  API Docs:  http://localhost:{args.port}/docs                ║
║  Storage:   {db_label:<49}║
╚══════════════════════════════════════════════════════════════╝
    """)

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
