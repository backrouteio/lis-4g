"""
ADMF — Administration Function
HI1 REST API (LEA-facing) + X1 provisioning (NE-facing).

Run:
    uvicorn admf.main:app --host 0.0.0.0 --port 8001 --reload
"""
import logging
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware

from admf.models import (
    ActivateWarrantRequest, DeactivateWarrantRequest,
    WarrantResponse, ActiveWarrantSummary,
)
from admf.x1_provisioner import X1Provisioner
from common.database import PostgresDB, RedisCache
from common.kafka_client import LISProducer, TOPIC_WARRANTS
from common.models import Warrant, TargetIdentifier, TargetIDType, InterceptType
from config.settings import Settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---- App setup ---- #
app = FastAPI(
    title="LIS ADMF — HI1 Interface",
    description="Administration Function for Lawful Interception (ETSI TS 103 221-1)",
    version="1.0.0",
)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# ---- Singletons (initialized on startup) ---- #
settings = Settings()
db = PostgresDB(dsn=settings.postgres_dsn)
cache = RedisCache(host=settings.redis_host, port=settings.redis_port)
producer = LISProducer(bootstrap_servers=settings.kafka_brokers)
provisioner = X1Provisioner(ne_endpoints=settings.ne_endpoints, producer=producer)


@app.on_event("startup")
def startup():
    db.connect()
    logger.info("ADMF started. Redis ping: %s", cache.ping())
    # Reload active warrants into Redis cache on restart
    for warrant in db.get_active_warrants():
        cache.add_active_liid(warrant.liid, warrant.target.value)
    logger.info("Active warrants reloaded into cache")


@app.on_event("shutdown")
def shutdown():
    producer.flush()
    producer.close()
    db.disconnect()


# ============================================================
# HI1 Endpoints
# ============================================================

@app.post("/hi1/warrants/activate", response_model=WarrantResponse, tags=["HI1"])
def activate_warrant(req: ActivateWarrantRequest):
    """
    HI1 — LEA activates a new lawful intercept warrant.
    ETSI TS 103 221-1: CreateDestinationRequest equivalent.
    """
    if req.valid_until <= datetime.utcnow():
        raise HTTPException(400, "valid_until must be in the future")

    warrant = Warrant(
        liid=req.liid,
        lea_id=req.lea_id,
        target=TargetIdentifier(
            id_type=TargetIDType(req.target.id_type.value),
            value=req.target.value,
        ),
        intercept_type=InterceptType(req.intercept_type.value),
        delivery_address=req.delivery_address,
        valid_from=req.valid_from,
        valid_until=req.valid_until,
    )

    # 1. Persist warrant
    db.save_warrant(warrant)

    # 2. Cache for fast NE-side lookup
    cache.add_active_liid(warrant.liid, warrant.target.value)

    # 3. Push X1 provisioning tasks to NEs
    provisioner.activate(warrant)

    # 4. Notify other services via Kafka
    producer.send_warrant_event({
        "event": "ACTIVATE",
        "liid": warrant.liid,
        "target_value": warrant.target.value,
        "target_id_type": warrant.target.id_type.value,
        "intercept_type": warrant.intercept_type.value,
        "delivery_address": warrant.delivery_address,
    })

    logger.info("Warrant ACTIVATED: LIID=%s target=%s", warrant.liid, warrant.target.value)
    return WarrantResponse(liid=req.liid, status="ACTIVATED", message="Intercept activated successfully")


@app.post("/hi1/warrants/deactivate", response_model=WarrantResponse, tags=["HI1"])
def deactivate_warrant(req: DeactivateWarrantRequest):
    """
    HI1 — LEA deactivates an existing warrant.
    """
    warrant = db.get_warrant(req.liid)
    if not warrant:
        raise HTTPException(404, f"Warrant {req.liid} not found")
    if warrant.lea_id != req.lea_id:
        raise HTTPException(403, "LEA ID mismatch")

    db.deactivate_warrant(req.liid)
    cache.remove_liid(req.liid, warrant.target.value)
    provisioner.deactivate(req.liid, warrant.target.value)

    producer.send_warrant_event({"event": "DEACTIVATE", "liid": req.liid})

    logger.info("Warrant DEACTIVATED: LIID=%s", req.liid)
    return WarrantResponse(liid=req.liid, status="DEACTIVATED", message="Intercept deactivated")


@app.get("/hi1/warrants", response_model=list[ActiveWarrantSummary], tags=["HI1"])
def list_active_warrants():
    """List all currently active warrants (admin/audit use)."""
    warrants = db.get_active_warrants()
    return [
        ActiveWarrantSummary(
            liid=w.liid, lea_id=w.lea_id,
            target_value=w.target.value,
            intercept_type=w.intercept_type.value,
            valid_until=w.valid_until,
            active=w.active,
        )
        for w in warrants
    ]


@app.get("/hi1/warrants/{liid}", response_model=ActiveWarrantSummary, tags=["HI1"])
def get_warrant(liid: str):
    warrant = db.get_warrant(liid)
    if not warrant:
        raise HTTPException(404, f"Warrant {liid} not found")
    return ActiveWarrantSummary(
        liid=warrant.liid, lea_id=warrant.lea_id,
        target_value=warrant.target.value,
        intercept_type=warrant.intercept_type.value,
        valid_until=warrant.valid_until,
        active=warrant.active,
    )


@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "service": "ADMF", "timestamp": datetime.utcnow().isoformat()}
