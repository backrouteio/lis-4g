"""
Kafka producer/consumer wrappers for inter-service event streaming.

Topics:
  lis.iri.events   — IRI records from IRI-MF (consumed by HI Gateway)
  lis.cc.events    — CC records from CC-MF   (consumed by HI Gateway)
  lis.x1.tasks     — X1 provisioning tasks from ADMF (consumed by NE adapters)
  lis.warrants     — Warrant activations/deactivations (consumed by IRI-MF, CC-MF)
"""
import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Callable

from kafka import KafkaProducer, KafkaConsumer
from kafka.errors import KafkaError

logger = logging.getLogger(__name__)

TOPIC_IRI = "lis.iri.events"
TOPIC_CC = "lis.cc.events"
TOPIC_X1 = "lis.x1.tasks"
TOPIC_WARRANTS = "lis.warrants"


def _serialize(obj):
    """JSON serializer that handles datetime and bytes."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, bytes):
        return obj.hex()
    raise TypeError(f"Type {type(obj)} not serializable")


class LISProducer:
    def __init__(self, bootstrap_servers: str = "localhost:9092"):
        self._producer = KafkaProducer(
            bootstrap_servers=bootstrap_servers,
            value_serializer=lambda v: json.dumps(v, default=_serialize).encode("utf-8"),
            acks="all",
            retries=3,
        )

    def send(self, topic: str, payload: dict, key: str = None):
        future = self._producer.send(
            topic,
            value=payload,
            key=key.encode("utf-8") if key else None,
        )
        try:
            future.get(timeout=10)
        except KafkaError as e:
            logger.error("Kafka send error on %s: %s", topic, e)
            raise

    def send_iri(self, iri_dict: dict):
        self.send(TOPIC_IRI, iri_dict, key=iri_dict.get("liid"))

    def send_cc(self, cc_dict: dict):
        self.send(TOPIC_CC, cc_dict, key=cc_dict.get("liid"))

    def send_x1_task(self, task_dict: dict):
        self.send(TOPIC_X1, task_dict, key=task_dict.get("liid"))

    def send_warrant_event(self, event: dict):
        self.send(TOPIC_WARRANTS, event, key=event.get("liid"))

    def flush(self):
        self._producer.flush()

    def close(self):
        self._producer.close()


class LISConsumer:
    def __init__(
        self,
        topics: list[str],
        group_id: str,
        bootstrap_servers: str = "localhost:9092",
        auto_offset_reset: str = "earliest",
    ):
        self._consumer = KafkaConsumer(
            *topics,
            bootstrap_servers=bootstrap_servers,
            group_id=group_id,
            value_deserializer=lambda v: json.loads(v.decode("utf-8")),
            auto_offset_reset=auto_offset_reset,
            enable_auto_commit=True,
        )

    def consume(self, handler: Callable[[str, dict], None], poll_timeout_ms: int = 1000):
        """Blocking consume loop. handler(topic, message_dict) called per message."""
        logger.info("Starting consume loop on %s", self._consumer.subscription())
        try:
            for msg in self._consumer:
                try:
                    handler(msg.topic, msg.value)
                except Exception as e:
                    logger.error("Handler error on topic %s: %s", msg.topic, e)
        except KeyboardInterrupt:
            pass
        finally:
            self._consumer.close()
