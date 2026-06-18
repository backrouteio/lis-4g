"""
CC-MF — Content of Communication Mediation Function
X3 receiver (from S-GW/P-GW) → HI3 delivery (to LEA via HI Gateway).

Run:
    uvicorn cc_mf.main:app --host 0.0.0.0 --port 8003 --reload
"""
import logging
import threading
from datetime import datetime

from fastapi import FastAPI

from cc_mf.x3_receiver import register_x3_routes
from common.database import PostgresDB, RedisCache
from common.kafka_client import LISProducer, LISConsumer, TOPIC_WARRANTS
from config.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="LIS CC-MF — X3/HI3 Interface",
    description="CC Mediation Function (ETSI TS 103 221-2 / 3GPP TS 33.107)",
    version="1.0.0",
)

settings = Settings()
db = PostgresDB(dsn=settings.postgres_dsn)
cache = RedisCache(host=settings.redis_host, port=settings.redis_port)
producer = LISProducer(bootstrap_servers=settings.kafka_brokers)

_seq_counters: dict[str, int] = {}


def _next_seq(liid: str) -> int:
    _seq_counters[liid] = _seq_counters.get(liid, 0) + 1
    return _seq_counters[liid]


# ---- Warrant sync consumer ---- #

def _warrant_event_handler(topic: str, msg: dict):
    event = msg.get("event")
    liid = msg.get("liid")
    if event == "ACTIVATE":
        cache.add_active_liid(liid, msg.get("target_value", ""))
    elif event == "DEACTIVATE":
        cache.remove_liid(liid, msg.get("target_value", ""))


def _start_warrant_consumer():
    consumer = LISConsumer(
        topics=[TOPIC_WARRANTS],
        group_id="cc-mf-warrant-consumer",
        bootstrap_servers=settings.kafka_brokers,
    )
    t = threading.Thread(target=consumer.consume, args=(_warrant_event_handler,), daemon=True)
    t.start()
    logger.info("CC-MF warrant consumer started")


# ---- Core CC processing ---- #

def process_cc_packet(packet_dict: dict):
    """
    Called by X3 receiver for each inbound CC packet.
    1. Validate LIID is active
    2. Wrap in HI3 envelope (ETSI TS 102 232-7 CC record)
    3. Publish to Kafka for HI Gateway → HI3 delivery
    """
    liid = packet_dict.get("liid")
    if not liid:
        raise ValueError("Missing LIID in X3 packet")

    if not cache.is_liid_active(liid):
        logger.warning("X3 packet for inactive/unknown LIID=%s — discarding", liid)
        return

    seq = _next_seq(liid)

    # Build HI3 CC record envelope
    cc_record = {
        "liid": liid,
        "sequence_number": seq,
        "timestamp": packet_dict.get("timestamp", datetime.utcnow().isoformat()),
        "direction": packet_dict.get("direction", "UNKNOWN"),
        "payload_hex": packet_dict.get("payload_hex", ""),
        "src_ip": packet_dict.get("src_ip"),
        "dst_ip": packet_dict.get("dst_ip"),
        "src_port": packet_dict.get("src_port"),
        "dst_port": packet_dict.get("dst_port"),
        "protocol": packet_dict.get("protocol"),
        "frame_length": packet_dict.get("frame_length"),
        # HI3 metadata
        "hi3_version": 2,
        "network_function": "CC-MF",
    }

    producer.send_cc(cc_record)
    logger.debug("CC queued for HI3: LIID=%s seq=%d %s→%s",
                 liid, seq, packet_dict.get("src_ip"), packet_dict.get("dst_ip"))


# ---- FastAPI lifecycle ---- #

@app.on_event("startup")
def startup():
    db.connect()
    for w in db.get_active_warrants():
        cache.add_active_liid(w.liid, w.target.value)
    _start_warrant_consumer()
    logger.info("CC-MF started")


@app.on_event("shutdown")
def shutdown():
    producer.flush()
    producer.close()
    db.disconnect()


register_x3_routes(app, process_cc_packet)


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "CC-MF", "timestamp": datetime.utcnow().isoformat()}
