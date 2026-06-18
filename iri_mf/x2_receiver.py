"""
X2 Receiver — IRI events from 4G Network Elements → IRI-MF.
ETSI TS 103 221-2 / 3GPP TS 33.108

Network elements (MME, HSS) POST IRI events to this endpoint.
IRI-MF validates the LIID, encodes to ASN.1, and queues for HI2 delivery.
"""
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)


# ---------- X2 message schema (simplified ETSI TS 103 221-2) ---------- #

class X2IRIEvent(BaseModel):
    """Inbound X2 IRI event from a Network Element."""
    liid: str
    sequence_number: int
    event_type: str                     # See IRIEventType enum
    timestamp: str                      # ISO8601
    # Target identifiers (at least one required)
    imsi: Optional[str] = None
    msisdn: Optional[str] = None
    imei: Optional[str] = None
    # Location
    cell_id: Optional[str] = None       # E-UTRAN Cell ID (ECGI)
    tai: Optional[str] = None           # Tracking Area Identity (MCC+MNC+TAC)
    # Bearer / session
    apn: Optional[str] = None
    ue_ip: Optional[str] = None
    bearer_id: Optional[str] = None
    qci: Optional[int] = None           # QoS Class Identifier
    # SMS specific
    sms_content: Optional[str] = None
    sms_direction: Optional[str] = None # "MO" or "MT"


def register_x2_routes(app: FastAPI, iri_handler):
    """Register X2 receiver routes onto an existing FastAPI app."""

    @app.post("/x2/iri", tags=["X2"])
    def receive_iri_event(event: X2IRIEvent):
        """
        X2 — Network Element delivers IRI event to IRI-MF.
        MME calls this on: Attach, Detach, TAU, Bearer setup/release, SMS.
        """
        logger.info("X2 IRI received: LIID=%s event=%s", event.liid, event.event_type)
        try:
            iri_handler(event.dict())
            return {"status": "accepted", "liid": event.liid, "seq": event.sequence_number}
        except ValueError as e:
            raise HTTPException(400, str(e))
        except Exception as e:
            logger.error("X2 IRI handler error: %s", e)
            raise HTTPException(500, "Internal processing error")

    @app.get("/x2/health", tags=["X2"])
    def x2_health():
        return {"status": "ok", "interface": "X2"}
