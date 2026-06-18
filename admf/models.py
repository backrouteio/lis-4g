"""
ADMF API request/response models (Pydantic) for HI1 interface.
ETSI TS 103 221-1 Section 6 — X1 / HI1 message structures.
"""
from datetime import datetime
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class TargetIDTypeEnum(str, Enum):
    MSISDN = "MSISDN"
    IMSI = "IMSI"
    IMEI = "IMEI"
    IP_ADDRESS = "IP_ADDRESS"


class InterceptTypeEnum(str, Enum):
    IRI_ONLY = "IRI_ONLY"
    CC_ONLY = "CC_ONLY"
    IRI_AND_CC = "IRI_AND_CC"


class TargetIdentifierRequest(BaseModel):
    id_type: TargetIDTypeEnum
    value: str = Field(..., example="447700900123")


class ActivateWarrantRequest(BaseModel):
    """HI1: LEA → ADMF — activate a new intercept."""
    liid: str = Field(..., example="LIID-2024-001", description="Unique Lawful Intercept ID")
    lea_id: str = Field(..., example="LEA-UK-001")
    target: TargetIdentifierRequest
    intercept_type: InterceptTypeEnum = InterceptTypeEnum.IRI_AND_CC
    valid_from: datetime
    valid_until: datetime
    delivery_address: str = Field(..., example="192.168.1.100:8443",
                                   description="LEA HI2/HI3 delivery endpoint")


class DeactivateWarrantRequest(BaseModel):
    """HI1: LEA → ADMF — deactivate an intercept."""
    liid: str
    lea_id: str


class WarrantResponse(BaseModel):
    liid: str
    status: str
    message: str


class ActiveWarrantSummary(BaseModel):
    liid: str
    lea_id: str
    target_value: str
    intercept_type: str
    valid_until: datetime
    active: bool
