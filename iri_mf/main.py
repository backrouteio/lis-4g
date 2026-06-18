"""
IRI-MF — IRI Mediation Function
X2 receiver (from NEs) + HI2 sender (to LEA via HI Gateway).

Run:
    uvicorn iri_mf.main:app --host 0.0.0.0 --port 8002 --reload
"""
import logging
import threading
from datetime import datetime

from fastapi import FastAPI

from iri_mf.x2_receiver import register_x2_routes
from iri_mf.asn1_encoder import IRI_ASN1Encoder
from common.database import PostgresDB, RedisCache
from common.kafka_client import LISProducer, LISConsumer, TOPIC_WARRANTS
from config.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LIS IRI-MF — X2/HI2 Interface",
    description="IRI Mediation Function (ETSI TS 102 232-7 / 3GPP TS 33.107)",
    version="1.0.0",
)

settings = Settings()
db = PostgresDB(dsn=settings.postgres_dsn)
cache = RedisCache(host=settings.redis_host, port=settings.redis_port)
producer = LISProducer(bootstrap_servers=settings.kafka_brokers)
encoder = IRI_ASN1Encoder()

# Running sequence counter per LIID (in-memory; use Redis incr in production)
_seq_counters: dict[str, int] = {}


def _next_seq(liid: str) -> int:
    _seq_counters[liid] = _seq_counters.get(liid, 0) + 1
    return _seq_counters[liid]


# ---- Warrant event consumer (keeps cache in sync) ---- #

def _warrant_event_handler(topic: str, msg: dict):
    event = msg.get("event")
    liid = msg.get("liid")
    if event == "ACTIVATE":
        cache.add_active_liid(liid, msg.get("target_value", ""))
        logger.info("IRI-MF: warrant activated for LIID=%s", liid)
    elif event == "DEACTIVATE":
        cache.remove_liid(liid, msg.get("target_value", ""))
        logger.info("IRI-MF: warrant deactivated for LIID=%s", liid)


def _start_warrant_consumer():
    consumer = LISConsumer(
        topics=[TOPIC_WARRANTS],
        group_id="iri-mf-warrant-consumer",
        bootstrap_servers=settings.kafka_brokers,
    )
    t = threading.Thread(target=consumer.consume, args=(_warrant_event_handler,), daemon=True)
    t.start()
    logger.info("IRI-MF warrant consumer started")


# ---- Core IRI processing ---- #

def process_iri_event(event_dict: dict):
    """
    Called by X2 receiver for each inbound IRI event.
    1. Validate LIID is active
    2. Enrich with sequence number
    3. ASN.1 encode
    4. Publish to Kafka for HI Gateway delivery
    """
    liid = event_dict.get("liid")
    if not liid:
        raise ValueError("Missing LIID in X2 event")

    # Check intercept is active (Redis fast path)
    if not cache.is_liid_active(liid):
        logger.warning("X2 event received for inactive/unknown LIID=%s — discarding", liid)
        return

    # Assign sequence number
    event_dict["sequence_number"] = _next_seq(liid)

    # Encode to ASN.1
    encoded_bytes = encoder.encode(event_dict)

    # Publish to HI Gateway via Kafka
    iri_record = {
        "liid": liid,
        "sequence_number": event_dict["sequence_number"],
        "event_type": event_dict.get("event_type"),
        "timestamp": event_dict.get("timestamp", datetime.utcnow().isoformat()),
        "asn1_payload_hex": encoded_bytes.hex(),    # HI Gateway will decode and send
        "raw": event_dict,
    }
    producer.send_iri(iri_record)
    logger.info("IRI encoded and queued for HI2: LIID=%s seq=%d event=%s",
                liid, event_dict["sequence_number"], event_dict.get("event_type"))


# ---- FastAPI lifecycle ---- #

@app.on_event("startup")
def startup():
    db.connect()
    # Seed cache from DB
    for w in db.get_active_warrants():
        cache.add_active_liid(w.liid, w.target.value)
    _start_warrant_consumer()
    logger.info("IRI-MF started")


@app.on_event("shutdown")
def shutdown():
    producer.flush()
    producer.close()
    db.disconnect()


# Register X2 routes
register_x2_routes(app, process_iri_event)


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "IRI-MF", "timestamp": datetime.utcnow().isoformat()}
