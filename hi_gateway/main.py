"""
HI Gateway — Delivery to LEA over HI2 (IRI) and HI3 (CC).
Consumes from Kafka (lis.iri.events, lis.cc.events) and delivers
to LEA endpoints per warrant's delivery_address.

ETSI TS 102 232-7 / 3GPP TS 33.108

Run:
    python -m hi_gateway.main
"""
import json
import logging
import ssl
import socket
import threading
from datetime import datetime

import httpx

from common.database import PostgresDB
from common.kafka_client import LISConsumer, TOPIC_IRI, TOPIC_CC
from config.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = Settings()
db = PostgresDB(dsn=settings.postgres_dsn)

# Cache: LIID → delivery_address (avoid DB hit per packet)
_delivery_cache: dict[str, str] = {}


def _get_delivery_address(liid: str) -> str | None:
    if liid in _delivery_cache:
        return _delivery_cache[liid]
    warrant = db.get_warrant(liid)
    if warrant and warrant.active:
        _delivery_cache[liid] = warrant.delivery_address
        return warrant.delivery_address
    return None


# ---------- HI2 Delivery ---------- #

def deliver_hi2(liid: str, iri_record: dict):
    """
    HI2: Send IRI record to LEA.
    Transport: TLS-secured HTTP POST per ETSI TS 102 232 TS2.
    """
    address = _get_delivery_address(liid)
    if not address:
        logger.warning("HI2: no delivery address for LIID=%s", liid)
        return

    payload = {
        "version": "ETSI-TS-102-232-7",
        "liid": liid,
        "sequence_number": iri_record.get("sequence_number"),
        "timestamp": iri_record.get("timestamp"),
        "event_type": iri_record.get("event_type"),
        "asn1_payload_hex": iri_record.get("asn1_payload_hex"),
    }

    _http_post(f"https://{address}/hi2/iri", payload, interface="HI2", liid=liid)


# ---------- HI3 Delivery ---------- #

def deliver_hi3(liid: str, cc_record: dict):
    """
    HI3: Send CC (content) record to LEA.
    Transport: TLS-secured HTTP POST (or UDP stream in high-throughput production).
    """
    address = _get_delivery_address(liid)
    if not address:
        logger.warning("HI3: no delivery address for LIID=%s", liid)
        return

    payload = {
        "version": "ETSI-TS-103-221-2",
        "liid": liid,
        "sequence_number": cc_record.get("sequence_number"),
        "timestamp": cc_record.get("timestamp"),
        "direction": cc_record.get("direction"),
        "payload_hex": cc_record.get("payload_hex"),
        "src_ip": cc_record.get("src_ip"),
        "dst_ip": cc_record.get("dst_ip"),
    }

    _http_post(f"https://{address}/hi3/cc", payload, interface="HI3", liid=liid)


def _http_post(url: str, payload: dict, interface: str, liid: str):
    """TLS POST to LEA endpoint. Retries once on failure."""
    for attempt in range(2):
        try:
            with httpx.Client(verify=settings.lea_ca_cert_path or False, timeout=10.0) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()
                logger.info("%s delivered: LIID=%s seq=%s status=%d",
                            interface, liid, payload.get("sequence_number"), r.status_code)
                return
        except Exception as e:
            logger.error("%s delivery failed (attempt %d) LIID=%s: %s",
                         interface, attempt + 1, liid, e)


# ---------- Kafka Consumer Loop ---------- #

def message_handler(topic: str, msg: dict):
    liid = msg.get("liid")
    if topic == TOPIC_IRI:
        deliver_hi2(liid, msg)
    elif topic == TOPIC_CC:
        deliver_hi3(liid, msg)


def run():
    db.connect()
    logger.info("HI Gateway started")

    consumer = LISConsumer(
        topics=[TOPIC_IRI, TOPIC_CC],
        group_id="hi-gateway-consumer",
        bootstrap_servers=settings.kafka_brokers,
    )
    try:
        consumer.consume(message_handler)
    except KeyboardInterrupt:
        logger.info("HI Gateway shutting down")
    finally:
        db.disconnect()


if __name__ == "__main__":
    run()
