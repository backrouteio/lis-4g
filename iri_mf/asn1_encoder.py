"""
ASN.1 Encoder for HI2 IRI records.
Based on ETSI TS 102 232-7 (PS LTE) and 3GPP TS 33.108 Annex B.

This module encodes IRI events into the ASN.1 BER format required
for HI2 delivery to the LEA.

For production: load the actual ASN.1 schema from 3GPP TS 33.108
using asn1tools:
    import asn1tools
    schema = asn1tools.compile_files(["ts33108.asn"])
    encoded = schema.encode("IRIContent", iri_data)

This implementation provides a structured dict representation
and a stub for the real ASN.1 encoding step.
"""
import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class IRI_ASN1Encoder:
    """
    Encodes IRI events into HI2-ready format.

    Production: replace _encode_asn1() with real asn1tools encoding
    from the 3GPP TS 33.108 schema.
    """

    # IRI event type codes per ETSI TS 102 232-7
    EVENT_CODE_MAP = {
        "ATTACH": 1,
        "DETACH": 2,
        "BEARER_ESTABLISH": 3,
        "BEARER_RELEASE": 4,
        "LOCATION_UPDATE": 5,
        "SMS": 10,
    }

    def encode(self, iri_dict: dict) -> bytes:
        """
        Encode an IRI event dict to ASN.1 BER bytes for HI2.
        Returns encoded bytes ready for HI2 delivery.
        """
        asn1_struct = self._build_asn1_structure(iri_dict)
        return self._encode_asn1(asn1_struct)

    def _build_asn1_structure(self, iri: dict) -> dict:
        """
        Build the ASN.1 structure per ETSI TS 102 232-7 Section 5.
        IRIContent ::= CHOICE {
            iRI-Begin-record    [1] IRI-Parameters,
            iRI-End-record      [2] IRI-Parameters,
            iRI-Continue-record [3] IRI-Parameters,
            iRI-Report-record   [4] IRI-Parameters
        }
        """
        event_type = iri.get("event_type", "UNKNOWN")
        event_code = self.EVENT_CODE_MAP.get(event_type, 99)

        # Map LI event types to IRI record type
        if event_type in ("ATTACH", "BEARER_ESTABLISH"):
            record_type = "iRI-Begin-record"
        elif event_type in ("DETACH", "BEARER_RELEASE"):
            record_type = "iRI-End-record"
        else:
            record_type = "iRI-Report-record"

        params = {
            "lawfulInterceptionIdentifier": iri.get("liid"),
            "sequenceNumber": iri.get("sequence_number", 0),
            "timeStamp": iri.get("timestamp"),
            "initiator": "not-available",
            "partyInformation": self._build_party_info(iri),
            "iRIversion": 5,                        # LTE/EPC version
            "networkElementIdentifier": "LIS-IRI-MF",
            "ePSEvent": {
                "eventType": event_code,
                "ePSSpecificParameters": self._build_eps_params(iri),
            },
        }

        return {record_type: params}

    def _build_party_info(self, iri: dict) -> list:
        party = {"partyQualifier": "target"}
        identifiers = []
        if iri.get("imsi"):
            identifiers.append({"iMSI": iri["imsi"]})
        if iri.get("msisdn"):
            identifiers.append({"mSISDN": iri["msisdn"]})
        if iri.get("imei"):
            identifiers.append({"iMEI": iri["imei"]})
        party["partyIdentity"] = identifiers
        return [party]

    def _build_eps_params(self, iri: dict) -> dict:
        params = {}
        if iri.get("cell_id"):
            params["eCGI"] = iri["cell_id"]
        if iri.get("tai"):
            params["trackingAreaId"] = iri["tai"]
        if iri.get("apn"):
            params["accessPointName"] = iri["apn"]
        if iri.get("ue_ip"):
            params["uEAddress"] = iri["ue_ip"]
        if iri.get("qci") is not None:
            params["qCI"] = iri["qci"]
        return params

    def _encode_asn1(self, structure: dict) -> bytes:
        """
        --- STUB ---
        In production, use asn1tools with the 3GPP TS 33.108 schema:

            import asn1tools
            schema = asn1tools.compile_files(["3gpp_33108_annex_b.asn"])
            return schema.encode("IRIContent", structure, "ber")

        For now, returns JSON-encoded bytes as a placeholder.
        """
        logger.debug("ASN.1 encoding (stub): %s", list(structure.keys()))
        return json.dumps(structure, default=str).encode("utf-8")
