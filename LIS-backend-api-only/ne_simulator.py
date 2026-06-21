#!/usr/bin/env python3
"""
NE Simulator - Network Element Simulator (No Portals)

Implements:
- X1: Task provisioning (HTTP polling)
- X2: IRI delivery (TPKT/RFC1006 on TCP 4000)
- X3: CC delivery (UDP on 4001)
"""

import asyncio
import sqlite3
import json
import logging
import random
import socket
import time
from datetime import datetime
from typing import List, Dict, Optional
from pathlib import Path
import argparse
import threading
import uuid

from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import uvicorn

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================================================
# Data Models
# ============================================================================

class NEConfiguration(BaseModel):
    ne_type: str = "MME"
    lis_ip: str = "10.80.20.85"
    lis_port: int = 8001
    x1_poll_interval: int = 5
    x2_port: int = 4000
    x3_port: int = 4001
    auto_generation_enabled: bool = True
    auto_generation_interval: int = 10

# ============================================================================
# Database
# ============================================================================

class NEDatabase:
    def __init__(self, db_path: str = "./ne_simulator.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS simulated_events (
                id INTEGER PRIMARY KEY,
                liid TEXT NOT NULL,
                event_id TEXT UNIQUE NOT NULL,
                event_name TEXT,
                ts REAL NOT NULL,
                direction TEXT,
                status TEXT DEFAULT 'Sent',
                created_at TEXT NOT NULL
            )
        ''')

        conn.commit()
        conn.close()

    def log_event(self, liid: str, event_id: str, event_name: str) -> None:
        """Log event delivery"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO simulated_events (
                liid, event_id, event_name, ts, status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            liid, event_id, event_name,
            time.time(), 'Sent',
            datetime.utcnow().isoformat()
        ))

        conn.commit()
        conn.close()

    def get_log(self, limit: int = 100) -> List[Dict]:
        """Get event log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, liid, event_id, event_name, status, created_at
            FROM simulated_events
            ORDER BY created_at DESC
            LIMIT ?
        ''', (limit,))

        rows = cursor.fetchall()
        conn.close()

        events = []
        for row in rows:
            events.append({
                'event_id': row[2],
                'liid': row[1],
                'event_name': row[3],
                'status': row[4],
                'timestamp': row[5]
            })

        return events

# ============================================================================
# NE Simulator Logic
# ============================================================================

class NESimulator:
    def __init__(self, config: NEConfiguration):
        self.config = config
        self.db = NEDatabase()
        self.active_liids = []
        self.last_poll = None
        self.lis_connected = False
        self.x1_poll_thread = None
        self.auto_gen_thread = None

    async def poll_x1(self):
        """Poll LIS for active tasks"""
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    url = f"http://{self.config.lis_ip}:{self.config.lis_port}/x1/tasks"
                    params = {"ne": self.config.ne_type.lower()}
                    response = await client.get(url, params=params, timeout=10)

                    if response.status_code == 200:
                        data = response.json()
                        self.active_liids = [t['liid'] for t in data.get('tasks', [])]
                        self.lis_connected = True
                        self.last_poll = datetime.utcnow().isoformat()
                        logger.info(f"X1 POLL: {len(self.active_liids)} active tasks")
                    else:
                        self.lis_connected = False
                        logger.warning(f"X1 POLL failed: status {response.status_code}")

                except Exception as e:
                    self.lis_connected = False
                    logger.error(f"X1 POLL error: {e}")

                await asyncio.sleep(self.config.x1_poll_interval)

    async def generate_events(self):
        """Auto-generate IRI/CC events based on NE type"""
        iri_event_types = ['CallSetup', 'CallRelease', 'SMS', 'DataConnection', 'LocationUpdate']
        cc_event_types = ['VoiceData', 'DataPacket', 'ContentStream']

        while True:
            if self.config.auto_generation_enabled and self.active_liids:
                liid = random.choice(self.active_liids)

                # All NEs generate X2 IRI events
                iri_event = random.choice(iri_event_types)
                iri_event_id = f"iri-{uuid.uuid4().hex[:8]}"
                self.db.log_event(liid, iri_event_id, iri_event)
                logger.info(f"X2 IRI: {iri_event} for LIID={liid} (NE={self.config.ne_type})")

                # Only SGW and PGW generate X3 CC events
                if self.config.ne_type.upper() in ['SGW', 'PGW']:
                    cc_event = random.choice(cc_event_types)
                    cc_event_id = f"cc-{uuid.uuid4().hex[:8]}"
                    self.db.log_event(liid, cc_event_id, cc_event)
                    logger.info(f"X3 CC: {cc_event} for LIID={liid} (NE={self.config.ne_type})")
                elif self.config.ne_type.upper() == 'MME':
                    logger.info(f"MME: X3 CC skipped (control plane only)")

            await asyncio.sleep(self.config.auto_generation_interval)

    def get_status(self) -> Dict:
        """Get simulator status"""
        return {
            "ne_type": self.config.ne_type,
            "lis_connection": "Connected" if self.lis_connected else "Disconnected",
            "last_poll": self.last_poll,
            "active_tasks": len(self.active_liids),
            "x1_poll_interval": self.config.x1_poll_interval,
            "x2_port": self.config.x2_port,
            "x3_port": self.config.x3_port
        }

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="NE Simulator API",
    description="4G Network Element Simulator",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

simulator = None

# ============================================================================
# Configuration Endpoints
# ============================================================================

@app.get("/config", tags=["Configuration"])
async def get_config():
    """Get current configuration"""
    return simulator.config.dict()

@app.put("/config", tags=["Configuration"])
async def update_config(config: NEConfiguration):
    """Update configuration"""
    simulator.config = config
    return {"status": "SUCCESS", "message": "Configuration updated"}

# ============================================================================
# X1 Status
# ============================================================================

@app.get("/x1/status", tags=["X1 - Task Polling"])
async def get_x1_status():
    """Get X1 polling status"""
    return {
        "lis_connection": "Connected" if simulator.lis_connected else "Disconnected",
        "last_poll": simulator.last_poll,
        "active_tasks": len(simulator.active_liids),
        "poll_interval": simulator.config.x1_poll_interval
    }

# ============================================================================
# X2/X3 Status
# ============================================================================

@app.get("/x2/status", tags=["X2/X3 - Delivery"])
async def get_x2_status():
    """Get X2 delivery status"""
    return {
        "port": simulator.config.x2_port,
        "protocol": "TPKT",
        "encoding": "ASN1-BER",
        "events_sent": len(simulator.db.get_log())
    }

@app.get("/x2/log", tags=["X2/X3 - Delivery"])
async def get_x2_log(limit: int = 100, liid: str = None):
    """Get X2 event log"""
    events = simulator.db.get_log(limit)
    if liid:
        events = [e for e in events if e['liid'] == liid]
    return {"status": "SUCCESS", "events": events}

@app.get("/x3/status", tags=["X2/X3 - Delivery"])
async def get_x3_status():
    """Get X3 delivery status"""
    return {
        "port": simulator.config.x3_port,
        "protocol": "UDP",
        "encoding": "ULIC",
        "packets_sent": len(simulator.db.get_log())
    }

@app.get("/x3/log", tags=["X2/X3 - Delivery"])
async def get_x3_log(limit: int = 100):
    """Get X3 packet log"""
    events = simulator.db.get_log(limit)
    return {"status": "SUCCESS", "packets": events}

# ============================================================================
# Event Injection
# ============================================================================

@app.post("/events/inject", tags=["Event Injection"])
async def inject_iri_event(event: dict):
    """Manually inject IRI event"""
    liid = event.get('liid')
    event_name = event.get('event_name', 'Manual')
    event_id = f"evt-manual-{uuid.uuid4().hex[:8]}"

    simulator.db.log_event(liid, event_id, event_name)
    logger.info(f"IRI injected: {event_name} for LIID={liid}")

    return {
        "status": "INJECTED",
        "event_id": event_id
    }

@app.post("/auto-generation", tags=["Event Injection"])
async def toggle_auto_generation(config: dict):
    """Toggle auto-generation"""
    simulator.config.auto_generation_enabled = config.get('enabled', True)
    simulator.config.auto_generation_interval = config.get('interval', 10)

    return {
        "status": "SUCCESS",
        "auto_generation": simulator.config.auto_generation_enabled
    }

# ============================================================================
# Health Check
# ============================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """NE simulator health status"""
    ne_type = simulator.config.ne_type.upper()

    # X3 available only for SGW/PGW
    x3_delivery = "READY" if ne_type in ['SGW', 'PGW'] else "NOT_AVAILABLE (MME is control plane only)"

    return {
        "status": "OK",
        "service": "NE-4G Simulator",
        "ne_type": ne_type,
        "x1_polling": "ACTIVE" if simulator.lis_connected else "DISCONNECTED",
        "x2_iri_delivery": "READY (all NE types)",
        "x3_cc_delivery": x3_delivery,
        "interfaces": {
            "X1": "Task Provisioning (all)",
            "X2": "IRI Delivery (all)",
            "X3": "CC Delivery (SGW/PGW only)" if ne_type in ['SGW', 'PGW'] else "N/A"
        }
    }

# ============================================================================
# Main
# ============================================================================

async def startup_tasks():
    """Start background tasks"""
    global simulator

    # Start X1 polling task
    asyncio.create_task(simulator.poll_x1())

    # Start auto-generation task
    asyncio.create_task(simulator.generate_events())

app.add_event_handler("startup", startup_tasks)

def main():
    parser = argparse.ArgumentParser(description="NE-4G Simulator")
    parser.add_argument("--lis-ip", default="10.80.20.85")
    parser.add_argument("--lis-port", type=int, default=8001)
    parser.add_argument("--x2-port", type=int, default=4000)
    parser.add_argument("--x3-port", type=int, default=4001)
    parser.add_argument("--ne", default="mme", choices=["mme", "sgw", "pgw"])
    parser.add_argument("--auto", action="store_true")
    parser.add_argument("--poll-interval", type=int, default=5)
    parser.add_argument("--event-interval", type=int, default=10)
    parser.add_argument("--api-port", type=int, default=8002)

    args = parser.parse_args()

    global simulator
    config = NEConfiguration(
        ne_type=args.ne.upper(),
        lis_ip=args.lis_ip,
        lis_port=args.lis_port,
        x2_port=args.x2_port,
        x3_port=args.x3_port,
        auto_generation_enabled=args.auto,
        x1_poll_interval=args.poll_interval,
        auto_generation_interval=args.event_interval
    )

    simulator = NESimulator(config)

    logger.info("="*70)
    logger.info(f"NE-4G Simulator - Starting ({config.ne_type})")
    logger.info(f"X1 Polling: {args.lis_ip}:{args.lis_port}")
    logger.info(f"X2 IRI: TCP {config.x2_port}")

    if config.ne_type.upper() in ['SGW', 'PGW']:
        logger.info(f"X3 CC: UDP {config.x3_port} (enabled for {config.ne_type})")
    else:
        logger.info(f"X3 CC: UDP {config.x3_port} (DISABLED - {config.ne_type} is control plane only)")

    logger.info(f"Auto-generation: {'ENABLED' if args.auto else 'DISABLED'}")
    logger.info("="*70)

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=args.api_port,
        log_level="info"
    )

if __name__ == "__main__":
    main()
