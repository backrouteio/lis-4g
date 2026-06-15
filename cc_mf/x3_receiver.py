"""
X3 Receiver — Content of Communication from S-GW/P-GW → CC-MF.
ETSI TS 103 221-2 Section 5.4 (X3 interface).

S-GW/P-GW mirrors intercepted user-plane packets (GTP-U payload)
and delivers them to CC-MF via two mechanisms:
  1. HTTP POST (this endpoint) — for low-volume or test scenarios
  2. UDP/TZSP encapsulation — for high-throughput production (see x3_udp_listener)
"""
import logging
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class X3CCPacket(BaseModel):
    """Inbound X3 CC packet from S-GW or P-GW."""
    liid: str
    sequence_number: int
    timestamp: str                      # ISO8601
    direction: str                      # "UPLINK" or "DOWNLINK"
    payload_hex: str                    # Raw IP packet as hex string
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    protocol: Optional[int] = None      # IP protocol (6=TCP, 17=UDP, etc.)
    frame_length: Optional[int] = None


def register_x3_routes(app: FastAPI, cc_handler):
    """Register X3 receiver routes onto an existing FastAPI app."""

    @app.post("/x3/cc", tags=["X3"])
    def receive_cc_packet(packet: X3CCPacket):
        """
        X3 — S-GW/P-GW delivers intercepted CC packet to CC-MF.
        Called per-packet or per-burst for each intercepted UE.
        """
        logger.debug("X3 CC: LIID=%s seq=%d dir=%s src=%s dst=%s",
                     packet.liid, packet.sequence_number,
                     packet.direction, packet.src_ip, packet.dst_ip)
        try:
            cc_handler(packet.dict())
            return {"status": "accepted", "liid": packet.liid, "seq": packet.sequence_number}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error("X3 CC handler error: %s", e)
            raise HTTPException(500, "Internal processing error")

    @app.post("/x3/cc/bulk", tags=["X3"])
    def receive_cc_bulk(packets: list[X3CCPacket]):
        """Bulk ingestion for high-throughput scenarios."""
        accepted = 0
        for pkt in packets:
            try:
                cc_handler(pkt.dict())
                accepted += 1
            except Exception as e:
                logger.error("Bulk X3 error on seq=%d: %s", pkt.sequence_number, e)
        return {"status": "accepted", "count": accepted}

    @app.get("/x3/health", tags=["X3"])
    def x3_health():
        return {"status": "ok", "interface": "X3"}
