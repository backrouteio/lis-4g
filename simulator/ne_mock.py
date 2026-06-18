"""
Mock Network Element Simulator — MME / S-GW / P-GW
Receives X1 provisioning tasks from ADMF and stores them in memory.
The portal polls /x1/tasks to display received tasks.

Each NE runs on its own port:
  MME  → port 9001   (python -m simulator.ne_mock --ne MME --port 9001)
  SGW  → port 9002   (python -m simulator.ne_mock --ne SGW --port 9002)
  PGW  → port 9003   (python -m simulator.ne_mock --ne PGW --port 9003)
"""
import argparse
import logging
from datetime import datetime
from typing import Optional

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── In-memory task store ─────────────────────────────────────────────────── #
_tasks: list[dict] = []


def make_app(ne_name: str) -> FastAPI:
    app = FastAPI(title=f"LIS NE Simulator — {ne_name}", version="1.0.0")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    # ─── X1 endpoint (ADMF calls this) ───────────────────────────────────── #

    class X1Task(BaseModel):
        task_id: str
        liid: str
        target_id_type: Optional[str] = None
        target_value: Optional[str] = None
        intercept_type: Optional[str] = None
        action: str                             # ACTIVATE | DEACTIVATE
        ne_address: Optional[str] = None
        timestamp: Optional[str] = None

    @app.post("/x1/intercept", tags=["X1"])
    def receive_x1_task(task: X1Task):
        """X1 — ADMF provisions intercept on this NE."""
        record = task.dict()
        record["ne_name"]     = ne_name
        record["received_at"] = datetime.utcnow().isoformat()
        _tasks.append(record)
        logger.info("[%s] X1 %s received: LIID=%s target=%s",
                    ne_name, task.action, task.liid, task.target_value)
        return {"status": "acknowledged", "ne": ne_name, "task_id": task.task_id}

    # ─── Portal polling endpoint ──────────────────────────────────────────── #

    @app.get("/x1/tasks", tags=["X1"])
    def list_x1_tasks():
        """Return all X1 tasks received by this NE (for portal display)."""
        return list(reversed(_tasks))          # most recent first

    @app.delete("/x1/tasks", tags=["X1"])
    def clear_x1_tasks():
        _tasks.clear()
        return {"cleared": True}

    @app.get("/health", tags=["System"])
    def health():
        return {"status": "ok", "ne": ne_name, "tasks_received": len(_tasks)}

    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ne",   default="MME", choices=["MME", "SGW", "PGW"])
    parser.add_argument("--port", type=int, default=9001)
    args = parser.parse_args()

    app = make_app(args.ne)
    logger.info("Starting NE simulator: %s on port %d", args.ne, args.port)
    uvicorn.run(app, host="0.0.0.0", port=args.port)
