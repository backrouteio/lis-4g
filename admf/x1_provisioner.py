"""
X1 Provisioner — ADMF → Network Elements.
Sends intercept activation/deactivation tasks to MME, S-GW, P-GW.
ETSI TS 103 221-1 Section 7 (X1 interface).

In production this would use TLS-secured HTTP/2 or Diameter.
This implementation uses HTTP REST as a simplified X1 transport.
"""
import logging
import uuid
from datetime import datetime
from typing import Optional
import httpx

from common.models import Warrant, X1Task, InterceptType
from common.kafka_client import LISProducer, TOPIC_X1

logger = logging.getLogger(__name__)


# Map intercept type to which NEs need to be provisioned
NE_ROUTING = {
    InterceptType.IRI_ONLY: ["MME"],
    InterceptType.CC_ONLY: ["SGW", "PGW"],
    InterceptType.IRI_AND_CC: ["MME", "SGW", "PGW"],
}


class X1Provisioner:
    def __init__(self, ne_endpoints: dict[str, str], producer: LISProducer):
        """
        ne_endpoints: {"MME": "http://mme:8080", "SGW": "http://sgw:8080", ...}
        """
        self._endpoints = ne_endpoints
        self._producer = producer

    def activate(self, warrant: Warrant):
        """Push X1 activation tasks to relevant NEs for a given warrant."""
        ne_list = NE_ROUTING.get(warrant.intercept_type, [])
        for ne_name in ne_list:
            task = X1Task(
                task_id=str(uuid.uuid4()),
                liid=warrant.liid,
                target=warrant.target,
                intercept_type=warrant.intercept_type,
                ne_address=self._endpoints.get(ne_name, ""),
                action="ACTIVATE",
            )
            self._send_task(ne_name, task)

    def deactivate(self, liid: str, target_value: str):
        """Push X1 deactivation to all NEs."""
        for ne_name, endpoint in self._endpoints.items():
            task_dict = {
                "task_id": str(uuid.uuid4()),
                "liid": liid,
                "target_value": target_value,
                "action": "DEACTIVATE",
                "ne_address": endpoint,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self._producer.send_x1_task(task_dict)
            logger.info("X1 DEACTIVATE sent to %s for LIID %s", ne_name, liid)

    def _send_task(self, ne_name: str, task: X1Task):
        """
        Publish X1 task to Kafka (NE adapters consume from lis.x1.tasks).
        In a real system, you'd also call the NE's X1 REST/Diameter endpoint directly.
        """
        task_dict = {
            "task_id": task.task_id,
            "liid": task.liid,
            "target_id_type": task.target.id_type.value,
            "target_value": task.target.value,
            "intercept_type": task.intercept_type.value,
            "ne_address": task.ne_address,
            "action": task.action,
            "timestamp": task.timestamp.isoformat(),
        }
        self._producer.send_x1_task(task_dict)
        logger.info("X1 %s task sent to %s for LIID %s", task.action, ne_name, task.liid)

        # Optionally: direct HTTP call to NE
        endpoint = self._endpoints.get(ne_name)
        if endpoint:
            self._http_notify(endpoint, task_dict)

    def _http_notify(self, endpoint: str, payload: dict):
        """Direct HTTP POST to NE X1 endpoint (best-effort)."""
        try:
            with httpx.Client(timeout=5.0) as client:
                r = client.post(f"{endpoint}/x1/intercept", json=payload)
                r.raise_for_status()
        except Exception as e:
            logger.warning("Direct X1 HTTP notify failed (%s): %s", endpoint, e)
