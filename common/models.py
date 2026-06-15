"""
Shared data models for LIS platform.
Based on 3GPP TS 33.107 / ETSI TS 103 221-1
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class InterceptType(str, Enum):
    IRI_ONLY = "IRI_ONLY"         # Intercept Related Information only
    CC_ONLY = "CC_ONLY"           # Content of Communication only
    IRI_AND_CC = "IRI_AND_CC"     # Both IRI and CC


class TargetIDType(str, Enum):
    MSISDN = "MSISDN"
    IMSI = "IMSI"
    IMEI = "IMEI"
    IP_ADDRESS = "IP_ADDRESS"


class IRIEventType(str, Enum):
    ATTACH = "ATTACH"
    DETACH = "DETACH"
    BEARER_ESTABLISH = "BEARER_ESTABLISH"
    BEARER_RELEASE = "BEARER_RELEASE"
    LOCATION_UPDATE = "LOCATION_UPDATE"
    SMS = "SMS"


@dataclass
class TargetIdentifier:
    id_type: TargetIDType
    value: str                    # e.g. "447700900123" for MSISDN


@dataclass
class Warrant:
    """
    Represents a lawful intercept warrant received over HI1.
    Maps to ETSI TS 103 221-1 XID/LIID structure.
    """
    liid: str                               # Lawful Intercept ID (unique per warrant)
    target: TargetIdentifier
    intercept_type: InterceptType
    lea_id: str                             # Issuing Law Enforcement Agency ID
    valid_from: datetime
    valid_until: datetime
    delivery_address: str                   # LEA endpoint for HI2/HI3
    created_at: datetime = field(default_factory=datetime.utcnow)
    active: bool = True


@dataclass
class IRIRecord:
    """
    IRI (Intercept Related Information) record from network element via X2.
    ETSI TS 102 232-7 / 3GPP TS 33.108
    """
    liid: str
    sequence_number: int
    event_type: IRIEventType
    timestamp: datetime
    imsi: Optional[str] = None
    msisdn: Optional[str] = None
    imei: Optional[str] = None
    cell_id: Optional[str] = None
    tai: Optional[str]  = None              # Tracking Area Identity
    apn: Optional[str] = None
    ue_ip: Optional[str] = None
    raw: Optional[dict] = None              # Raw event dict from NE


@dataclass
class CCRecord:
    """
    CC (Content of Communication) record from S-GW/P-GW via X3.
    ETSI TS 103 221-2
    """
    liid: str
    sequence_number: int
    timestamp: datetime
    direction: str                          # "UPLINK" or "DOWNLINK"
    payload: bytes                          # Raw IP packet payload
    src_ip: Optional[str] = None
    dst_ip: Optional[str] = None
    src_port: Optional[int] = None
    dst_port: Optional[int] = None
    protocol: Optional[int] = None         # IP protocol number


@dataclass
class X1Task:
    """Provisioning task sent from ADMF to a Network Element over X1."""
    task_id: str
    liid: str
    target: TargetIdentifier
    intercept_type: InterceptType
    ne_address: str                         # Network element endpoint
    action: str                             # "ACTIVATE" or "DEACTIVATE"
    timestamp: datetime = field(default_factory=datetime.utcnow)
