"""
NE Simulator — India 4G LTE Lawful Interception
Simulates xGW / GGSN / IAP-PGW / MME network elements.

Standards compliance:
  TS 33.106, TS 33.107, TS 33.108 (3GPP)
  RFC 1006 / RFC 2126 — ISO Transport on TCP (TPKT) for X2

Interfaces:
  X1  — SSH-based CLI (simulated as HTTP poll) → LIS :8001
  X2  — TPKT/TCP → LIS :4000  (ASN.1 BER IRI, TS 33.108 Annex B.9)
  X3  — UDP/TCP  → LIS :4001  (ULIC CC header, TS 33.108 Annex C)
        UDP: ULICv0 (§C.1.2) or Nokia ULICv08
        TCP: ULICv1 (§C.1.3) or ULICv09

Usage:
    python ne_simulator.py --lis-ip 76.13.211.64 --ne GGSN

Event table per Table 12 (TS 33.107 / TS 33.108):
  xGW    Bearer activation/modification/deactivation + Start-of-Intercept
  GGSN   PDP context activation/modification/deactivation + Start-of-Intercept
  PGW    PMIP session events
  MME    Attach/Detach/TAU/Handover
"""

import argparse
import collections
import json
import logging
import socket
import struct
import threading
import time
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("NE-SIM")


# ═══════════════════════════════════════════════════════════════
#  CONSTANTS
# ═══════════════════════════════════════════════════════════════

# IRI record types (TS 33.108)
IRI_BEGIN    = 0   # New intercept target session started
IRI_END      = 1   # Session ended
IRI_CONTINUE = 2   # Mid-session update
IRI_REPORT   = 3   # Unsuccessful attempt / report-only

# X2 event table (Table 12, TS 33.108)
# Format: (description, gprs_event_code, iri_record_type)
#  GPRS event codes per TS 33.108 Annex B.9  (GPRS-IRI ENUMERATED)
EVENTS = {
    # ── xGW / EPS bearer events (TS 33.107 §12.2.x / 33.108 §10.5) ──
    "xGW_BEARER_ACTIVATION":             ("Bearer activation (successful)",         1,  IRI_BEGIN),
    "xGW_BEARER_MODIFICATION":           ("Bearer modification",                    7,  IRI_CONTINUE),
    "xGW_BEARER_RESOURCE_MODIFICATION":  ("UE requested bearer resource mod",       7,  IRI_REPORT),
    "xGW_BEARER_ACTIVATION_FAIL":        ("Bearer activation (unsuccessful)",       1,  IRI_REPORT),
    "xGW_START_INTERCEPT_ACTIVE_BEARER": ("Start of interception, active bearer",   5,  IRI_BEGIN),
    "xGW_BEARER_DEACTIVATION":           ("Bearer deactivation",                    4,  IRI_END),
    # ── GGSN / GPRS PDP context events (TS 33.107 §7.4.x / 33.108 §6.5) ──
    "GGSN_PDP_ACTIVATION":               ("PDP context activation (successful)",    1,  IRI_BEGIN),
    "GGSN_PDP_MODIFICATION":             ("PDP context modification",               7,  IRI_CONTINUE),
    "GGSN_PDP_ACTIVATION_FAIL":          ("PDP context activation (unsuccessful)",  1,  IRI_REPORT),
    "GGSN_START_INTERCEPT_ACTIVE_PDP":   ("Start of interception, PDP active",      5,  IRI_BEGIN),
    "GGSN_PDP_DEACTIVATION":             ("PDP context deactivation",               4,  IRI_END),
    # ── IAP-PGW / PMIP tunnel events (TS 33.107 §12.4.x / 33.108 §10.5) ──
    "PGW_PMIP_ACTIVATION":               ("PMIP attach/tunnel activation",          1,  IRI_BEGIN),
    "PGW_PMIP_ACTIVATION_FAIL":          ("PMIP attach/tunnel activation (fail)",   1,  IRI_REPORT),
    "PGW_PMIP_MODIFICATION":             ("PMIP session modification",              7,  IRI_CONTINUE),
    "PGW_PMIP_DEACTIVATION":             ("PMIP detach/tunnel deactivation",        4,  IRI_END),
    "PGW_START_INTERCEPT_ACTIVE_PMIP":   ("Start of interception, active PMIP",     5,  IRI_BEGIN),
    "PGW_RESOURCE_DEACTIVATION":         ("PMIP resource allocation deactivation",  4,  IRI_END),
    # ── MME EPS events (TS 33.107 §12.x) ──
    "MME_ATTACH":                        ("UE Attach",                              1,  IRI_BEGIN),
    "MME_DETACH":                        ("UE Detach",                              4,  IRI_END),
    "MME_TAU":                           ("Tracking Area Update",                   5,  IRI_CONTINUE),
    "MME_HANDOVER":                      ("Handover",                               7,  IRI_CONTINUE),
    "MME_SMS":                           ("SMS over NAS",                           5,  IRI_REPORT),
    "MME_LOCATION":                      ("Location Update / IMSI Detach",          5,  IRI_REPORT),
}

# Default auto-demo event sequence per NE type
NE_EVENTS = {
    "GGSN": [
        "GGSN_PDP_ACTIVATION", "GGSN_START_INTERCEPT_ACTIVE_PDP",
        "GGSN_PDP_MODIFICATION", "GGSN_PDP_DEACTIVATION",
    ],
    "XGW":  [
        "xGW_BEARER_ACTIVATION", "xGW_START_INTERCEPT_ACTIVE_BEARER",
        "xGW_BEARER_MODIFICATION", "xGW_BEARER_DEACTIVATION",
    ],
    "PGW":  [
        "PGW_PMIP_ACTIVATION", "PGW_START_INTERCEPT_ACTIVE_PMIP",
        "PGW_PMIP_MODIFICATION", "PGW_PMIP_DEACTIVATION",
    ],
    "MME":  [
        "MME_ATTACH", "MME_LOCATION",
        "MME_TAU", "MME_HANDOVER", "MME_DETACH",
    ],
    "SGW":  [
        "xGW_BEARER_ACTIVATION", "xGW_START_INTERCEPT_ACTIVE_BEARER",
        "xGW_BEARER_DEACTIVATION",
    ],
}

# ULIC versions (TS 33.108 Annex C)
ULIC_V0   = 0x00   # §C.1.2 — UDP transport (standard)
ULIC_V08  = 0x08   # Nokia/ALU vendor extended — UDP
ULIC_V1   = 0x01   # §C.1.3 — TCP transport
ULIC_V09  = 0x09   # ULICv09 — UDP/TCP

# ULIC content types
CONTENT_IPv4     = 0x01
CONTENT_IPv6     = 0x02
CONTENT_ETHERNET = 0x03
CONTENT_TCP      = 0x04
CONTENT_UDP      = 0x05

# ULIC direction
UPLINK   = 0x00   # target → network
DOWNLINK = 0x01   # network → target


# ═══════════════════════════════════════════════════════════════
#  ASN.1 BER ENCODER  (TS 33.108 Annex B.9 — GPRS-IRI)
# ═══════════════════════════════════════════════════════════════

def _ber_len(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    if n < 0x100:
        return bytes([0x81, n])
    return bytes([0x82, n >> 8, n & 0xFF])


def _ctx(tag_num: int, value: bytes, constructed: bool = False) -> bytes:
    """Context-tagged IMPLICIT TLV (single-byte context tag)."""
    tag = 0x80 | (0x20 if constructed else 0x00) | (tag_num & 0x1F)
    return bytes([tag]) + _ber_len(len(value)) + value


def _enc_int(n: int) -> bytes:
    if n == 0:
        return b'\x00'
    result = []
    while n:
        result.append(n & 0xFF)
        n >>= 8
    result.reverse()
    if result[0] & 0x80:
        result.insert(0, 0x00)
    return bytes(result)


def _enc_generalizedtime(dt: datetime) -> bytes:
    return dt.strftime("%Y%m%d%H%M%SZ").encode("ascii")


def _enc_utf8(s: str) -> bytes:
    return s.encode("utf-8")


def _enc_octet(s: str) -> bytes:
    return s.encode("ascii")


def encode_iri_asn1(params: dict) -> bytes:
    """
    Encode GPRS-IRI as ASN.1 BER per TS 33.108 Annex B.9.
    Uses IMPLICIT tagging (per spec requirement).
    All known identities (IMSI, MSISDN, IMEI, NAI) are included when available.

    params:
        liid          str  — LI Identifier (mandatory; sent even though LIG ignores it per spec)
        event_key     str  — key into EVENTS dict
        iri_type      int  — IRI_BEGIN / IRI_CONTINUE / IRI_END / IRI_REPORT
        gprs_code     int  — GPRS event enumeration code
        imsi          str
        msisdn        str
        imei          str  (optional)
        nai           str  (optional) — Network Access Identifier
        seq           int
        ue_ip         str  (optional)
        apn           str  (optional)
        cell_id       str  (optional)
        bearer_id     int  (optional)
        qci           int  (optional)
        teid          str  (optional) — Tunnel Endpoint ID (hex)
        rat_type      int  (optional) — Radio Access Type (6 = E-UTRAN)
    """
    now = datetime.now(timezone.utc)
    fields = b""

    # [0] iriVersion INTEGER ::= 1
    fields += _ctx(0, _enc_int(1))

    # [1] timeStamp GeneralizedTime
    fields += _ctx(1, _enc_generalizedtime(now))

    # [2] liID UTF8String  (mandatory; LIID ignored by LIG/DF2 per spec but populated)
    fields += _ctx(2, _enc_utf8(params.get("liid", "LIID-UNKNOWN")))

    # [3] sequenceNumber INTEGER
    fields += _ctx(3, _enc_int(params.get("seq", 0)))

    # [4] iriType ENUMERATED { iRI-Begin(0), iRI-End(1), iRI-Continue(2), iRI-Report(3) }
    fields += _ctx(4, _enc_int(params.get("iri_type", IRI_BEGIN)))

    # [5] gPRSEvent ENUMERATED (per Annex B.9 GPRS-IRI)
    fields += _ctx(5, _enc_int(params.get("gprs_code", 1)))

    # [6] targetIMSI OCTET STRING (mandatory when available per spec)
    if params.get("imsi"):
        fields += _ctx(6, _enc_octet(params["imsi"]))

    # [7] targetMSISDN OCTET STRING (included when available)
    if params.get("msisdn"):
        fields += _ctx(7, _enc_octet(params["msisdn"]))

    # [8] targetIMEI OCTET STRING (included when available)
    if params.get("imei"):
        fields += _ctx(8, _enc_octet(params["imei"]))

    # [9] cellID — location information
    if params.get("cell_id"):
        fields += _ctx(9, _enc_octet(params["cell_id"]))

    # [10] ueIPAddress
    if params.get("ue_ip"):
        fields += _ctx(10, _enc_octet(params["ue_ip"]))

    # [11] accessPointName
    if params.get("apn"):
        fields += _ctx(11, _enc_utf8(params["apn"]))

    # [12] bearerID / PDPContextID
    if params.get("bearer_id") is not None:
        fields += _ctx(12, _enc_int(params["bearer_id"]))

    # [13] qCI — QoS Class Identifier
    if params.get("qci") is not None:
        fields += _ctx(13, _enc_int(params["qci"]))

    # [14] nAI — Network Access Identifier (when available per spec)
    if params.get("nai"):
        fields += _ctx(14, _enc_utf8(params["nai"]))

    # [15] rATType — Radio Access Technology
    if params.get("rat_type") is not None:
        fields += _ctx(15, _enc_int(params["rat_type"]))

    # [16] tEID — Tunnel Endpoint Identifier
    if params.get("teid"):
        try:
            teid_bytes = bytes.fromhex(params["teid"].replace("0x", "").replace(" ", ""))
            fields += _ctx(16, teid_bytes)
        except Exception:
            pass

    # Wrap in SEQUENCE (tag 0x30)
    return b'\x30' + _ber_len(len(fields)) + fields


# ═══════════════════════════════════════════════════════════════
#  TPKT FRAMING  (RFC 1006 / RFC 2126)
#  X2 interface uses ISO Transport Service on top of TCP (ITOT/TPKT)
#  because TCP is stream-based with no inherent message delineation.
# ═══════════════════════════════════════════════════════════════

TPKT_VERSION = 0x03   # RFC 1006 §6 version field

def tpkt_wrap(payload: bytes) -> bytes:
    """
    Wrap payload in TPKT header (RFC 1006):
      Byte 0: version = 3
      Byte 1: reserved = 0
      Bytes 2-3: total packet length big-endian (includes 4-byte header itself)
    """
    total_len = 4 + len(payload)
    header = struct.pack(">BBH", TPKT_VERSION, 0, total_len)
    return header + payload


def tpkt_unwrap(buf: bytes):
    """
    Extract one TPKT message from buffer.
    Returns (payload, remaining_buf) or (None, buf) if incomplete.
    """
    if len(buf) < 4:
        return None, buf
    ver, _reserved, total_len = struct.unpack(">BBH", buf[:4])
    if ver != TPKT_VERSION:
        return None, buf[1:]   # skip bad byte
    if len(buf) < total_len:
        return None, buf
    payload   = buf[4:total_len]
    remaining = buf[total_len:]
    return payload, remaining


# ═══════════════════════════════════════════════════════════════
#  ULIC HEADER BUILDERS  (TS 33.108 Annex C)
# ═══════════════════════════════════════════════════════════════

def _ulic_build(version: int, liid: str, payload: bytes,
                direction: int, content_type: int, seq: int,
                two_byte_liid: bool) -> bytes:
    """
    Common ULIC header builder:
      1B version | 1B hdr_len | 2B pdu_len | 4B seq | 4B timestamp
      (1B or 2B) liid_len | N liid | 1B direction | 1B content_type
    """
    liid_b   = liid.encode("ascii")
    ll_sz    = 2 if two_byte_liid else 1
    hdr_len  = 1 + 1 + 2 + 4 + 4 + ll_sz + len(liid_b) + 1 + 1
    ts       = int(time.time())

    hdr  = struct.pack(">BBHII", version, hdr_len, len(payload), seq, ts)
    hdr += struct.pack(">H" if two_byte_liid else ">B", len(liid_b))
    hdr += liid_b
    hdr += struct.pack(">BB", direction, content_type)
    return hdr + payload


def build_ulic_v0(liid, seq, payload, direction=UPLINK, ct=CONTENT_IPv4):
    """ULICv0 — UDP, TS 33.108 §C.1.2 (1-byte liid_len, version=0x00)."""
    return _ulic_build(ULIC_V0, liid, payload, direction, ct, seq, False)


def build_ulic_v08(liid, seq, payload, direction=UPLINK, ct=CONTENT_IPv4):
    """ULICv08 — Nokia/ALU UDP vendor extension (version=0x08, 1-byte liid_len)."""
    return _ulic_build(ULIC_V08, liid, payload, direction, ct, seq, False)


def build_ulic_v1(liid, seq, payload, direction=UPLINK, ct=CONTENT_IPv4):
    """ULICv1 — TCP, TS 33.108 §C.1.3 (2-byte liid_len, version=0x01)."""
    return _ulic_build(ULIC_V1, liid, payload, direction, ct, seq, True)


def build_ulic_v09(liid, seq, payload, direction=UPLINK, ct=CONTENT_IPv4):
    """ULICv09 — TS 33.108 (2-byte liid_len, version=0x09)."""
    return _ulic_build(ULIC_V09, liid, payload, direction, ct, seq, True)


ULIC_BUILDERS = {
    "v0":  build_ulic_v0,
    "v08": build_ulic_v08,
    "v1":  build_ulic_v1,
    "v09": build_ulic_v09,
}


def _fake_ipv4_packet(src_ip: str, dst_ip: str, size: int = 64) -> bytes:
    """Minimal fake IPv4 packet for CC simulation."""
    def ip4(s):
        parts = [int(x) for x in s.split(".")]
        return struct.pack("BBBB", *parts)
    payload  = bytes(max(0, size - 20))
    src = ip4(src_ip) if src_ip and "." in src_ip else b'\x0a\x00\x00\x01'
    dst = ip4(dst_ip) if dst_ip and "." in dst_ip else b'\x08\x08\x08\x08'
    pkt = struct.pack(">BBHHHBBH4s4s",
        0x45, 0, 20 + len(payload), 0, 0, 64, 17, 0, src, dst) + payload
    return pkt


# ═══════════════════════════════════════════════════════════════
#  X1 POLLER  (HTTP poll → LIS provisioning tasks)
# ═══════════════════════════════════════════════════════════════

def _poll_x1(lis_ip, lis_port, ne_type, active_targets, lock, token=""):
    import urllib.request
    url = f"http://{lis_ip}:{lis_port}/x1/tasks/{ne_type.lower()}"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    poll_count    = 0
    fail_count    = 0
    logger.info("X1 poller starting: url=%s auth=%s", url, "bearer" if token else "none")
    while True:
        poll_count += 1
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=5) as r:
                status = r.status
                tasks = json.loads(r.read())
                new_count = 0
                if isinstance(tasks, list):
                    with lock:
                        for t in tasks:
                            if not isinstance(t, dict):
                                continue
                            liid = t.get("liid") or t.get("li_id")
                            if liid and liid not in active_targets:
                                active_targets[liid] = t
                                new_count += 1
                                logger.info("X1 ← NEW TASK  liid=%-20s imsi=%s type=%s",
                                            liid, t.get("target_imsi","?"),
                                            t.get("intercept_type","IRI+CC"))
                if fail_count:
                    logger.info("X1 poll: recovered after %d failed attempt(s)", fail_count)
                fail_count = 0
                logger.debug("X1 poll #%d: HTTP %s, %d task(s) returned, %d new, %d active total",
                             poll_count, status, len(tasks) if isinstance(tasks, list) else -1,
                             new_count, len(active_targets))
        except Exception as e:
            fail_count += 1
            if fail_count in (1, 5) or fail_count % 20 == 0:
                logger.warning("X1 poll failed (%d consecutive): %s — url=%s", fail_count, e, url)
            else:
                logger.debug("X1 poll: %s", e)
        time.sleep(5)


# ═══════════════════════════════════════════════════════════════
#  X2 TCP CLIENT  (TPKT/RFC1006 + ASN.1 BER IRI)
# ═══════════════════════════════════════════════════════════════

class X2Client:
    """
    Persistent TPKT/TCP connection to LIS X2 port.
    Per spec: IRI messages are buffered when DF2 is unreachable
    and flushed once connection is restored (X2 IRI Cache).
    """

    RECONNECT_DELAY = 5    # seconds
    MAX_BUFFER      = 500  # max cached frames

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port
        self._sock      = None
        self._lock      = threading.Lock()
        self._seq       = 0
        self._buf: collections.deque = collections.deque(maxlen=self.MAX_BUFFER)
        self._connected = False

    def connect(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(10)
            s.connect((self.host, self.port))
            s.settimeout(None)
            with self._lock:
                self._sock      = s
                self._connected = True
            logger.info("X2 TCP: connected %s:%d (TPKT/RFC1006 framing)", self.host, self.port)
            self._flush_buffer()
            return True
        except Exception as e:
            logger.warning("X2 TCP: connect failed (%s) — IRI cache active", e)
            return False

    def _disconnect(self):
        with self._lock:
            if self._sock:
                try: self._sock.close()
                except: pass
                self._sock = None
            was_connected = self._connected
            self._connected = False
        if was_connected:
            logger.debug("X2 TCP: disconnected from %s:%d (buffered=%d)", self.host, self.port, len(self._buf))

    def _flush_buffer(self):
        flushed = 0
        while self._buf:
            frame = self._buf.popleft()
            try:
                self._sock.sendall(frame)
                flushed += 1
            except Exception as e:
                self._buf.appendleft(frame)
                logger.warning("X2 flush failed: %s", e)
                self._disconnect()
                return
        if flushed:
            logger.info("X2 TCP: flushed %d cached IRI message(s) to DF2", flushed)

    def send_iri(self, params: dict) -> bool:
        """
        Encode params → ASN.1 BER → TPKT frame → TCP.
        Buffers frame (IRI cache) if DF2 is unreachable.
        """
        params["seq"] = self._seq
        self._seq += 1
        asn1_bytes = encode_iri_asn1(params)
        frame      = tpkt_wrap(asn1_bytes)   # RFC 1006 TPKT framing
        logger.debug("X2 encode: liid=%s event=%s seq=%d ber_bytes=%d tpkt_bytes=%d params=%s",
                     params.get("liid","?"), params.get("event_key","?"), params["seq"],
                     len(asn1_bytes), len(frame), {k:v for k,v in params.items() if k not in ("event_key",)})

        with self._lock:
            connected = self._connected
            sock      = self._sock

        if not connected:
            self._buf.append(frame)
            logger.info("X2 IRI cached (DF2 offline): liid=%s event=%s seq=%d buffer_depth=%d/%d",
                        params.get("liid","?"), params.get("event_key","?"), params["seq"],
                        len(self._buf), self.MAX_BUFFER)
            threading.Thread(target=self._bg_reconnect, daemon=True).start()
            return False

        try:
            sock.sendall(frame)
            iri_names = {IRI_BEGIN:"BEGIN", IRI_END:"END",
                         IRI_CONTINUE:"CONTINUE", IRI_REPORT:"REPORT"}
            desc = EVENTS.get(params.get("event_key",""), ("?",))[0]
            iri  = iri_names.get(params.get("iri_type", IRI_BEGIN), "?")
            logger.info("X2 TCP IRI → liid=%-18s %-40s %-8s [%dB BER+TPKT] seq=%d imsi=%s msisdn=%s",
                        params.get("liid","?"), desc, iri, len(asn1_bytes), params["seq"],
                        params.get("imsi","?"), params.get("msisdn","?"))
            return True
        except Exception as e:
            logger.warning("X2 TCP send failed: %s — caching IRI liid=%s seq=%d", e,
                           params.get("liid","?"), params["seq"])
            self._buf.append(frame)
            self._disconnect()
            threading.Thread(target=self._bg_reconnect, daemon=True).start()
            return False

    def _bg_reconnect(self):
        logger.debug("X2 TCP: scheduling reconnect to %s:%d in %ds", self.host, self.port, self.RECONNECT_DELAY)
        time.sleep(self.RECONNECT_DELAY)
        self.connect()


# ═══════════════════════════════════════════════════════════════
#  X3 UDP CLIENT  (ULIC CC — TS 33.108 §C.1.2)
# ═══════════════════════════════════════════════════════════════

class X3UDPClient:
    def __init__(self, host: str, port: int, ulic_version: str = "v0"):
        self.host    = host
        self.port    = port
        self.version = ulic_version.lower()
        self._sock   = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._seq    = 0

    def send_cc(self, liid, payload=None, direction=UPLINK,
                content_type=CONTENT_IPv4, src_ip="10.45.0.1", dst_ip="8.8.8.8"):
        if payload is None:
            payload = _fake_ipv4_packet(src_ip, dst_ip)
        builder = ULIC_BUILDERS.get(self.version, build_ulic_v0)
        try:
            pkt = builder(liid, self._seq, payload, direction, content_type)
            self._sock.sendto(pkt, (self.host, self.port))
            dir_s = "UL" if direction == UPLINK else "DL"
            logger.info("X3 UDP ULIC%-3s → liid=%-18s seq=%-4d %s %dB CC → %s:%d",
                        self.version, liid, self._seq, dir_s, len(payload), self.host, self.port)
            logger.debug("X3 UDP packet: liid=%s seq=%d src_ip=%s dst_ip=%s content_type=%d hdr+payload=%dB hex_preview=%s",
                         liid, self._seq, src_ip, dst_ip, content_type, len(pkt), pkt[:32].hex())
            self._seq += 1
            return True
        except Exception as e:
            logger.error("X3 UDP send failed: liid=%s seq=%d dest=%s:%d — %s",
                         liid, self._seq, self.host, self.port, e)
            return False


# ═══════════════════════════════════════════════════════════════
#  X3 TCP CLIENT  (ULICv1/v09 — TS 33.108 §C.1.3)
# ═══════════════════════════════════════════════════════════════

class X3TCPClient:
    def __init__(self, host: str, port: int, ulic_version: str = "v1"):
        self.host    = host
        self.port    = port
        self.version = ulic_version.lower()
        self._sock   = None
        self._lock   = threading.Lock()
        self._seq    = 0

    def connect(self) -> bool:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((self.host, self.port))
            with self._lock:
                self._sock = s
            logger.info("X3 TCP: connected %s:%d (ULIC%s)", self.host, self.port, self.version)
            return True
        except Exception as e:
            logger.warning("X3 TCP: connect failed: %s", e)
            return False

    def send_cc(self, liid, payload=None, direction=UPLINK,
                content_type=CONTENT_IPv4, src_ip="10.45.0.1", dst_ip="8.8.8.8"):
        if payload is None:
            payload = _fake_ipv4_packet(src_ip, dst_ip)
        builder = ULIC_BUILDERS.get(self.version, build_ulic_v1)
        pkt = builder(liid, self._seq, payload, direction, content_type)
        try:
            with self._lock:
                if not self._sock:
                    logger.debug("X3 TCP: no active socket, reconnecting before send")
                    self.connect()
                self._sock.sendall(pkt)
            dir_s = "UL" if direction == UPLINK else "DL"
            logger.info("X3 TCP ULIC%-3s → liid=%-18s seq=%-4d %s %dB CC → %s:%d",
                        self.version, liid, self._seq, dir_s, len(payload), self.host, self.port)
            logger.debug("X3 TCP packet: liid=%s seq=%d src_ip=%s dst_ip=%s content_type=%d hdr+payload=%dB hex_preview=%s",
                         liid, self._seq, src_ip, dst_ip, content_type, len(pkt), pkt[:32].hex())
            self._seq += 1
            return True
        except Exception as e:
            logger.error("X3 TCP send failed: liid=%s seq=%d dest=%s:%d — %s",
                         liid, self._seq, self.host, self.port, e)
            with self._lock:
                self._sock = None
            return False


def _status_logger(active_targets, lock, x2, x3_udp, x3_tcp, interval=15):
    """Periodic heartbeat so operators can confirm the simulator is alive and connected."""
    while True:
        time.sleep(interval)
        with lock:
            n_targets = len(active_targets)
        x2_state  = "connected" if x2._connected else "disconnected (caching)"
        x3t_state = "connected" if x3_tcp._sock else "disconnected"
        logger.info("STATUS: active_targets=%d  X2_TCP=%s(seq=%d,buffered=%d)  X3_UDP=ready(seq=%d)  X3_TCP=%s(seq=%d)",
                    n_targets, x2_state, x2._seq, len(x2._buf), x3_udp._seq, x3t_state, x3_tcp._seq)


# ═══════════════════════════════════════════════════════════════
#  INTERACTIVE CLI
# ═══════════════════════════════════════════════════════════════

def _print_menu(ne_type: str):
    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║  NE Simulator — India 4G LTE LIS  [{ne_type}]
╠══════════════════════════════════════════════════════════════════════╣
║  X2 IRI — GGSN (GPRS PDP context, TS 33.108 §6.5)                 ║
║   1. PDP Context Activation          → IRI-BEGIN                    ║
║   2. Start of Interception (PDP up)  → IRI-BEGIN                    ║
║   3. PDP Context Modification        → IRI-CONTINUE                 ║
║   4. PDP Context Deactivation        → IRI-END                      ║
║   5. PDP Activation unsuccessful     → IRI-REPORT                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  X2 IRI — xGW/SGW (EPS bearer, TS 33.108 §10.5)                   ║
║   6. Bearer Activation               → IRI-BEGIN                    ║
║   7. Start of Interception (bearer)  → IRI-BEGIN                    ║
║   8. Bearer Modification             → IRI-CONTINUE                 ║
║   9. Bearer Deactivation             → IRI-END                      ║
║  10. Bearer resource mod             → IRI-REPORT                   ║
╠══════════════════════════════════════════════════════════════════════╣
║  X2 IRI — IAP-PGW/PMIP (TS 33.108 §10.5)                          ║
║  11. PMIP Tunnel Activation          → IRI-BEGIN                    ║
║  12. PMIP Session Modification       → IRI-CONTINUE                 ║
║  13. PMIP Tunnel Deactivation        → IRI-END                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  X3 CC — UDP transport (TS 33.108 Annex C)                         ║
║  20. UDP ULICv0  uplink    (§C.1.2 standard)                        ║
║  21. UDP ULICv0  downlink                                           ║
║  22. UDP ULICv08 uplink    (Nokia/ALU vendor ext)                   ║
║  23. UDP ULICv09 uplink    (per TS 33.108 v09)                      ║
╠══════════════════════════════════════════════════════════════════════╣
║  X3 CC — TCP transport (TS 33.108 §C.1.3)                          ║
║  25. TCP ULICv1  uplink                                             ║
║  26. TCP ULICv1  downlink                                           ║
╠══════════════════════════════════════════════════════════════════════╣
║   s. Show active intercept targets (X1)                             ║
║   a. Auto-demo: all events for first target                         ║
║   m. Show this menu                                                 ║
║   q. Quit                                                           ║
╚══════════════════════════════════════════════════════════════════════╝""")


CLI_MAP = {
    "1":  "GGSN_PDP_ACTIVATION",
    "2":  "GGSN_START_INTERCEPT_ACTIVE_PDP",
    "3":  "GGSN_PDP_MODIFICATION",
    "4":  "GGSN_PDP_DEACTIVATION",
    "5":  "GGSN_PDP_ACTIVATION_FAIL",
    "6":  "xGW_BEARER_ACTIVATION",
    "7":  "xGW_START_INTERCEPT_ACTIVE_BEARER",
    "8":  "xGW_BEARER_MODIFICATION",
    "9":  "xGW_BEARER_DEACTIVATION",
    "10": "xGW_BEARER_RESOURCE_MODIFICATION",
    "11": "PGW_PMIP_ACTIVATION",
    "12": "PGW_PMIP_MODIFICATION",
    "13": "PGW_PMIP_DEACTIVATION",
}


def _pick_target(active_targets, lock):
    with lock:
        targets = list(active_targets.items())
    if not targets:
        liid   = input("  LIID   : ").strip() or "TEST-LIID-01"
        imsi   = input("  IMSI   : ").strip() or "404100123456789"
        msisdn = input("  MSISDN : ").strip() or "+919962917824"
        return liid, {"liid": liid, "target_imsi": imsi, "target_msisdn": msisdn}
    if len(targets) == 1:
        return targets[0]
    for i, (liid, t) in enumerate(targets):
        print(f"  [{i}] {liid}  IMSI={t.get('target_imsi','?')}")
    idx = int(input("  Select [0]: ") or "0")
    return targets[min(idx, len(targets)-1)]


def _build_params(t, event_key, args):
    _desc, gprs_code, iri_type = EVENTS[event_key]
    liid = t.get("liid") or t.get("li_id", "TEST-LIID")
    logger.debug("Building IRI params: liid=%s event_key=%s gprs_code=%d iri_type=%d", liid, event_key, gprs_code, iri_type)
    return {
        "liid":      liid,
        "event_key": event_key,
        "gprs_code": gprs_code,
        "iri_type":  iri_type,
        "imsi":      t.get("target_imsi", "404100123456789"),
        "msisdn":    t.get("target_msisdn", "+919962917824"),
        "imei":      t.get("target_imei", ""),
        "nai":       t.get("target_nai", ""),
        "cell_id":   args.cell_id,
        "apn":       args.apn,
        "ue_ip":     args.ue_ip,
        "bearer_id": 5,
        "qci":       9,
        "rat_type":  6,   # 6 = E-UTRAN
    }


def _run_cli(ne_type, x2, x3_udp, x3_tcp, active_targets, lock, args):
    _print_menu(ne_type)

    while True:
        try:
            choice = input("\nNE> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break

        if choice == "q":
            break

        elif choice == "m":
            _print_menu(ne_type)

        elif choice == "s":
            with lock:
                if not active_targets:
                    print("  No active targets yet — waiting for X1 task from LIS...")
                else:
                    print(f"  Active targets ({len(active_targets)}):")
                    for liid, t in active_targets.items():
                        print(f"    LIID={liid}  IMSI={t.get('target_imsi','?')}  "
                              f"MSISDN={t.get('target_msisdn','?')}  "
                              f"TYPE={t.get('intercept_type','IRI+CC')}")

        elif choice == "a":
            with lock:
                targets = list(active_targets.items())
            if not targets:
                print("  No active targets. Activate a warrant via LEA portal first.")
                continue
            liid, t   = targets[0]
            demo_seq  = NE_EVENTS.get(ne_type, NE_EVENTS["GGSN"])
            print(f"\n  [AUTO-DEMO] LIID={liid} — {len(demo_seq)} IRI events + CC")
            for ek in demo_seq:
                p = _build_params(t, ek, args)
                x2.send_iri(p)
                time.sleep(0.4)
                _, _, iri_t = EVENTS[ek]
                if iri_t in (IRI_BEGIN, IRI_CONTINUE):
                    x3_udp.send_cc(liid, src_ip=args.ue_ip, dst_ip="8.8.8.8")
                    x3_udp.send_cc(liid, src_ip="8.8.8.8", dst_ip=args.ue_ip,
                                   direction=DOWNLINK)
                time.sleep(0.4)
            print("  [AUTO-DEMO] Done.")

        elif choice in CLI_MAP:
            event_key = CLI_MAP[choice]
            liid, t   = _pick_target(active_targets, lock)
            x2.send_iri(_build_params(t, event_key, args))

        elif choice in ("20", "21", "22", "23"):
            liid, _ = _pick_target(active_targets, lock)
            ue_ip   = args.ue_ip
            if choice == "20":
                pkt = build_ulic_v0(liid, x3_udp._seq, _fake_ipv4_packet(ue_ip, "8.8.8.8"))
                x3_udp._sock.sendto(pkt, (args.lis_ip, args.x3_port))
                logger.info("X3 UDP ULICv0  UL → liid=%s", liid); x3_udp._seq += 1
            elif choice == "21":
                pkt = build_ulic_v0(liid, x3_udp._seq, _fake_ipv4_packet("8.8.8.8", ue_ip),
                                    DOWNLINK)
                x3_udp._sock.sendto(pkt, (args.lis_ip, args.x3_port))
                logger.info("X3 UDP ULICv0  DL → liid=%s", liid); x3_udp._seq += 1
            elif choice == "22":
                pkt = build_ulic_v08(liid, x3_udp._seq, _fake_ipv4_packet(ue_ip, "8.8.8.8"))
                x3_udp._sock.sendto(pkt, (args.lis_ip, args.x3_port))
                logger.info("X3 UDP ULICv08 UL → liid=%s", liid); x3_udp._seq += 1
            elif choice == "23":
                pkt = build_ulic_v09(liid, x3_udp._seq, _fake_ipv4_packet(ue_ip, "8.8.8.8"))
                x3_udp._sock.sendto(pkt, (args.lis_ip, args.x3_port))
                logger.info("X3 UDP ULICv09 UL → liid=%s", liid); x3_udp._seq += 1

        elif choice in ("25", "26"):
            liid, _ = _pick_target(active_targets, lock)
            if choice == "25":
                x3_tcp.send_cc(liid, src_ip=args.ue_ip, dst_ip="8.8.8.8", direction=UPLINK)
            else:
                x3_tcp.send_cc(liid, src_ip="8.8.8.8", dst_ip=args.ue_ip, direction=DOWNLINK)

        else:
            print("  Unknown option. Type m for menu, q to quit.")


# ═══════════════════════════════════════════════════════════════
#  ENTRYPOINT
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="NE Simulator — India 4G LTE LIS (TS 33.106/107/108, RFC 1006/2126)")
    parser.add_argument("--lis-ip",      default="127.0.0.1")
    parser.add_argument("--lis-port",    type=int, default=8001, help="LIS HTTP port (X1)")
    parser.add_argument("--x2-port",     type=int, default=4000, help="LIS X2 TPKT/TCP port")
    parser.add_argument("--x3-port",     type=int, default=4001, help="LIS X3 UDP port")
    parser.add_argument("--x3-tcp-port", type=int, default=4002, help="LIS X3 TCP port")
    parser.add_argument("--ne",  default="GGSN",
                        choices=["MME","SGW","PGW","GGSN","XGW",
                                 "mme","sgw","pgw","ggsn","xgw"])
    parser.add_argument("--ulic",  default="v0",
                        choices=["v0","v08","v1","v09"],
                        help="Default ULIC version for X3 UDP")
    parser.add_argument("--cell-id", default="4041-001-0x1A2B")
    parser.add_argument("--apn",     default="internet")
    parser.add_argument("--ue-ip",   default="10.45.0.12")
    parser.add_argument("--token",   default="", help="LIS Bearer auth token")
    parser.add_argument("--auto",    action="store_true",
                        help="Auto-demo mode: send events automatically on X1 task")
    args = parser.parse_args()

    ne_type = args.ne.upper()

    print(f"""
╔══════════════════════════════════════════════════════════════════════╗
║   NE Simulator — India 4G LTE Lawful Interception                  ║
║   Network Element: {ne_type:<10}                                     ║
╠══════════════════════════════════════════════════════════════════════╣
║   TS 33.106 / TS 33.107 / TS 33.108 | RFC 1006 / RFC 2126         ║
╠══════════════════════════════════════════════════════════════════════╣
║   X1  HTTP poll → {args.lis_ip}:{args.lis_port}/x1/tasks/{ne_type.lower()}
║   X2  TPKT/TCP → {args.lis_ip}:{args.x2_port}  (ASN.1 BER, Table 12)
║   X3  UDP      → {args.lis_ip}:{args.x3_port}  (ULIC{args.ulic}, §C.1.2)
║       TCP      → {args.lis_ip}:{args.x3_tcp_port}  (ULICv1,  §C.1.3)
╚══════════════════════════════════════════════════════════════════════╝
""")

    active_targets: dict = {}
    lock = threading.Lock()

    # X1 poller
    threading.Thread(
        target=_poll_x1,
        args=(args.lis_ip, args.lis_port, ne_type, active_targets, lock, args.token),
        daemon=True, name="X1-poll"
    ).start()
    logger.info("X1 poll → %s:%d/x1/tasks/%s (every 5s)", args.lis_ip, args.lis_port, ne_type.lower())

    # X2 TCP (TPKT)
    x2 = X2Client(args.lis_ip, args.x2_port)
    x2.connect()

    # X3 UDP
    x3_udp = X3UDPClient(args.lis_ip, args.x3_port, ulic_version=args.ulic)
    logger.info("X3 UDP ready → %s:%d (ULIC%s)", args.lis_ip, args.x3_port, args.ulic)

    # X3 TCP (ULICv1)
    x3_tcp = X3TCPClient(args.lis_ip, args.x3_tcp_port, ulic_version="v1")
    x3_tcp.connect()

    # Periodic status heartbeat (every 15s) for live validation
    threading.Thread(
        target=_status_logger,
        args=(active_targets, lock, x2, x3_udp, x3_tcp),
        daemon=True, name="status-log"
    ).start()

    if args.auto:
        logger.info("AUTO-DEMO: events sent automatically on X1 task receipt")
        seen = set()
        while True:
            time.sleep(3)
            with lock:
                new = {k: v for k, v in active_targets.items() if k not in seen}
            for liid, t in new.items():
                seen.add(liid)
                demo_seq = NE_EVENTS.get(ne_type, NE_EVENTS["GGSN"])
                logger.info("AUTO: event sequence for LIID=%s (%d events)", liid, len(demo_seq))
                for ek in demo_seq:
                    p = _build_params(t, ek, args)
                    x2.send_iri(p)
                    time.sleep(1)
                    _, _, iri_t = EVENTS[ek]
                    if iri_t in (IRI_BEGIN, IRI_CONTINUE):
                        x3_udp.send_cc(liid, src_ip=args.ue_ip, dst_ip="8.8.8.8")
                        x3_udp.send_cc(liid, src_ip="8.8.8.8", dst_ip=args.ue_ip,
                                       direction=DOWNLINK)
                    time.sleep(1)
    else:
        _run_cli(ne_type, x2, x3_udp, x3_tcp, active_targets, lock, args)
