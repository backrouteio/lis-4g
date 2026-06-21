#!/usr/bin/env python3
"""
LIS-4G Backend API - Standalone Server (No Portals)

Implements:
- HI1: Warrant activation/deactivation (REST API)
- X1: Task provisioning (HTTP polling)
- X2: IRI delivery (TPKT/RFC1006 on TCP 4000)
- X3: CC delivery (UDP on 4001)
- HI3: FTP delivery to LEA
"""

import sqlite3
import json
import asyncio
import logging
import struct
import socket
import uuid
import threading
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pathlib import Path
import argparse

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
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

class DeliveryEndpoint(BaseModel):
    address: str
    port: int
    protocol: str = "FTP"

class HI1Parameters(BaseModel):
    sender_identifier: str
    receiver_identifier: str
    transaction_id: Optional[str] = None
    action_identifier: int = 0
    timestamp: Optional[str] = None
    object_identifier: Optional[str] = None

class WarrantCreateRequest(BaseModel):
    liid: str
    warrant_reference: str
    warrant_status: str = "Active"
    warrant_start_date: str
    warrant_end_date: str
    target_identifier_value: str
    target_identifier_type: str  # MSISDN, IMSI, IMEI, Email
    delivery_endpoint: DeliveryEndpoint
    hi1_parameters: HI1Parameters

class WarrantUpdateRequest(BaseModel):
    liid: str
    warrant_reference: Optional[str] = None
    warrant_end_date: Optional[str] = None
    delivery_endpoint: Optional[DeliveryEndpoint] = None
    hi1_parameters: HI1Parameters

class Warrant(BaseModel):
    liid: str
    warrant_reference: str
    warrant_status: str
    warrant_start_date: str
    warrant_end_date: str
    target_identifier_value: str
    target_identifier_type: str
    delivery_endpoint: DeliveryEndpoint
    hi1_parameters: HI1Parameters

# ============================================================================
# Database
# ============================================================================

class Database:
    def __init__(self, db_path: str = "./lis_standalone.db"):
        self.db_path = db_path
        self.init_db()

    def init_db(self):
        """Initialize database schema"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        # Warrants table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS warrants (
                id INTEGER PRIMARY KEY,
                liid TEXT UNIQUE NOT NULL,
                warrant_reference TEXT NOT NULL,
                warrant_status TEXT DEFAULT 'Active',
                warrant_start_date TEXT NOT NULL,
                warrant_end_date TEXT NOT NULL,
                target_identifier_value TEXT NOT NULL,
                target_identifier_type TEXT NOT NULL,
                delivery_address TEXT NOT NULL,
                delivery_port INTEGER NOT NULL,
                delivery_protocol TEXT DEFAULT 'FTP',
                sender_identifier TEXT NOT NULL,
                receiver_identifier TEXT NOT NULL,
                transaction_id TEXT,
                action_identifier INTEGER DEFAULT 0,
                object_identifier TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        ''')

        # IRI events log table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS iri_events (
                id INTEGER PRIMARY KEY,
                liid TEXT NOT NULL,
                event_id TEXT UNIQUE NOT NULL,
                event_name TEXT,
                ts REAL NOT NULL,
                calling_party TEXT,
                called_party TEXT,
                imsi TEXT,
                imei TEXT,
                cell_id TEXT,
                status TEXT DEFAULT 'Pending',
                created_at TEXT NOT NULL
            )
        ''')

        # CC packets log table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS cc_packets (
                id INTEGER PRIMARY KEY,
                liid TEXT NOT NULL,
                packet_id TEXT UNIQUE NOT NULL,
                packet_size INTEGER,
                ts REAL NOT NULL,
                direction TEXT,
                status TEXT DEFAULT 'Pending',
                created_at TEXT NOT NULL
            )
        ''')

        conn.commit()
        conn.close()

    def add_warrant(self, warrant: WarrantCreateRequest) -> Dict:
        """Add warrant to database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()

        try:
            cursor.execute('''
                INSERT INTO warrants (
                    liid, warrant_reference, warrant_status,
                    warrant_start_date, warrant_end_date,
                    target_identifier_value, target_identifier_type,
                    delivery_address, delivery_port, delivery_protocol,
                    sender_identifier, receiver_identifier,
                    transaction_id, action_identifier, object_identifier,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                warrant.liid, warrant.warrant_reference, warrant.warrant_status,
                warrant.warrant_start_date, warrant.warrant_end_date,
                warrant.target_identifier_value, warrant.target_identifier_type,
                warrant.delivery_endpoint.address, warrant.delivery_endpoint.port,
                warrant.delivery_endpoint.protocol,
                warrant.hi1_parameters.sender_identifier,
                warrant.hi1_parameters.receiver_identifier,
                warrant.hi1_parameters.transaction_id or str(uuid.uuid4()),
                warrant.hi1_parameters.action_identifier,
                warrant.hi1_parameters.object_identifier or f"auth-{uuid.uuid4().hex[:8]}",
                now, now
            ))
            conn.commit()
            logger.info(f"Warrant created: LIID={warrant.liid}")
            return {"status": "SUCCESS", "liid": warrant.liid}
        except sqlite3.IntegrityError:
            logger.error(f"Warrant already exists: {warrant.liid}")
            raise HTTPException(status_code=409, detail=f"Warrant {warrant.liid} already exists")
        finally:
            conn.close()

    def update_warrant(self, liid: str, update_req: WarrantUpdateRequest) -> Dict:
        """Update existing warrant"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()

        cursor.execute('SELECT * FROM warrants WHERE liid = ?', (liid,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"Warrant {liid} not found")

        updates = []
        values = []

        if update_req.warrant_reference:
            updates.append("warrant_reference = ?")
            values.append(update_req.warrant_reference)
        if update_req.warrant_end_date:
            updates.append("warrant_end_date = ?")
            values.append(update_req.warrant_end_date)
        if update_req.delivery_endpoint:
            updates.append("delivery_address = ?, delivery_port = ?, delivery_protocol = ?")
            values.extend([
                update_req.delivery_endpoint.address,
                update_req.delivery_endpoint.port,
                update_req.delivery_endpoint.protocol
            ])

        updates.append("updated_at = ?")
        values.append(now)
        values.append(liid)

        cursor.execute(f"UPDATE warrants SET {', '.join(updates)} WHERE liid = ?", values)
        conn.commit()
        conn.close()

        logger.info(f"Warrant updated: LIID={liid}")
        return {"status": "SUCCESS", "liid": liid}

    def delete_warrant(self, liid: str) -> Dict:
        """Deactivate warrant (soft delete)"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()

        cursor.execute('SELECT * FROM warrants WHERE liid = ?', (liid,))
        if not cursor.fetchone():
            conn.close()
            raise HTTPException(status_code=404, detail=f"Warrant {liid} not found")

        cursor.execute(
            'UPDATE warrants SET warrant_status = ?, updated_at = ? WHERE liid = ?',
            ('Inactive', now, liid)
        )
        conn.commit()
        conn.close()

        logger.info(f"Warrant deactivated: LIID={liid}")
        return {"status": "SUCCESS", "message": f"Warrant {liid} deactivated"}

    def get_warrants(self, status: str = "All") -> List[Dict]:
        """Get all warrants"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        if status == "All":
            cursor.execute('SELECT * FROM warrants')
        else:
            cursor.execute('SELECT * FROM warrants WHERE warrant_status = ?', (status,))

        rows = cursor.fetchall()
        conn.close()

        warrants = []
        for row in rows:
            warrants.append({
                'liid': row[1],
                'warrant_reference': row[2],
                'warrant_status': row[3],
                'warrant_start_date': row[4],
                'warrant_end_date': row[5],
                'target_identifier_value': row[6],
                'target_identifier_type': row[7],
                'delivery_endpoint': {
                    'address': row[8],
                    'port': row[9],
                    'protocol': row[10]
                },
                'hi1_parameters': {
                    'sender_identifier': row[11],
                    'receiver_identifier': row[12],
                    'transaction_id': row[13],
                    'action_identifier': row[14],
                    'object_identifier': row[15]
                }
            })

        return warrants

    def log_iri_event(self, liid: str, event_id: str, event_data: Dict) -> None:
        """Log IRI delivery event"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO iri_events (
                liid, event_id, event_name, ts,
                calling_party, called_party, imsi, imei, cell_id,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            liid, event_id, event_data.get('event_name'), datetime.utcnow().timestamp(),
            event_data.get('calling_party'), event_data.get('called_party'),
            event_data.get('imsi'), event_data.get('imei'), event_data.get('cell_id'),
            'Delivered', datetime.utcnow().isoformat()
        ))

        conn.commit()
        conn.close()

    def log_iri_event(self, liid: str, event_id: str, event_name: str) -> None:
        """Log X2 IRI event received from NE"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()

        try:
            cursor.execute('''
                INSERT INTO iri_events (
                    liid, event_id, event_name, ts, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (liid, event_id, event_name, __import__('time').time(), 'Delivered', now))
            conn.commit()
            logger.info(f"X2 RECEIVED: {event_name} (ID:{event_id}) for LIID={liid}")
        except sqlite3.IntegrityError:
            logger.warning(f"Duplicate event: {event_id}")
        finally:
            conn.close()

    def get_iri_log(self) -> List[Dict]:
        """Get IRI delivery log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, liid, event_id, event_name, ts, status, created_at
            FROM iri_events
            ORDER BY created_at DESC
            LIMIT 100
        ''')

        rows = cursor.fetchall()
        conn.close()

        events = []
        for row in rows:
            events.append({
                'event_id': row[2],
                'liid': row[1],
                'event_name': row[3],
                'timestamp': row[6],
                'status': row[5]
            })

        return events

    def clear_iri_log(self) -> int:
        """Clear IRI event log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM iri_events')
        count = cursor.fetchone()[0]

        cursor.execute('DELETE FROM iri_events')
        conn.commit()
        conn.close()

        logger.info(f"Cleared {count} IRI events from log")
        return count

    def log_cc_packet(self, liid: str, packet_id: str, packet_size: int) -> None:
        """Log CC packet delivery"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO cc_packets (
                liid, packet_id, packet_size, ts,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            liid, packet_id, packet_size, datetime.utcnow().timestamp(),
            'Delivered', datetime.utcnow().isoformat()
        ))

        conn.commit()
        conn.close()

    def get_cc_log(self) -> List[Dict]:
        """Get CC packet delivery log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('''
            SELECT id, liid, packet_id, packet_size, ts, status, created_at
            FROM cc_packets
            ORDER BY created_at DESC
            LIMIT 100
        ''')

        rows = cursor.fetchall()
        conn.close()

        packets = []
        for row in rows:
            packets.append({
                'packet_id': row[2],
                'liid': row[1],
                'packet_size': row[3],
                'timestamp': row[6],
                'status': row[5]
            })

        return packets

    def clear_cc_log(self) -> int:
        """Clear CC packet log"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute('SELECT COUNT(*) FROM cc_packets')
        count = cursor.fetchone()[0]

        cursor.execute('DELETE FROM cc_packets')
        conn.commit()
        conn.close()

        logger.info(f"Cleared {count} CC packets from log")
        return count

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="LIS-4G Backend API",
    description="4G LTE Lawful Interception System - Backend Only",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

# Global database instance
db = Database()

# ============================================================================
# HI1 Endpoints - Warrant Management
# ============================================================================

@app.post("/hi1/warrants/activate", tags=["HI1 - Warrant Management"])
async def create_warrant(warrant: WarrantCreateRequest):
    """
    CREATE action in HI1 - Activate new intercept warrant

    Parameters include full HI1 structure with:
    - LIID: Lawful Interception Identifier
    - SenderIdentifier: LEA endpoint ID
    - ReceiverIdentifier: CSP/Network endpoint ID
    - TransactionID: Unique request UUID
    - ActionIdentifier: Sequential action number
    """
    result = db.add_warrant(warrant)
    logger.info(f"HI1 CREATE: LIID={warrant.liid}, SenderID={warrant.hi1_parameters.sender_identifier}")
    return {"status": result["status"], "liid": result["liid"], "message": "Warrant activated"}

@app.put("/hi1/warrants/update", tags=["HI1 - Warrant Management"])
async def update_warrant(update_req: WarrantUpdateRequest):
    """
    UPDATE action in HI1 - Modify existing warrant details
    """
    result = db.update_warrant(update_req.liid, update_req)
    logger.info(f"HI1 UPDATE: LIID={update_req.liid}, SenderID={update_req.hi1_parameters.sender_identifier}")
    return {"status": result["status"], "liid": result["liid"], "message": "Warrant updated"}

@app.delete("/hi1/warrants/delete", tags=["HI1 - Warrant Management"])
async def delete_warrant(liid: str = Query(...)):
    """
    DELETE action in HI1 - Deactivate warrant, stop intercept
    """
    result = db.delete_warrant(liid)
    logger.info(f"HI1 DELETE: LIID={liid}")
    return result

@app.get("/hi1/warrants/list", tags=["HI1 - Warrant Management"])
async def list_warrants(status: str = Query("All", enum=["Active", "Inactive", "All"])):
    """
    LIST action in HI1 - Query warrant status
    """
    warrants = db.get_warrants(status)
    logger.info(f"HI1 LIST: status={status}, count={len(warrants)}")
    return {
        "status": "SUCCESS",
        "warrants": warrants,
        "count": len(warrants)
    }

# ============================================================================
# X1 Endpoints - Task Provisioning
# ============================================================================

@app.get("/x1/tasks", tags=["X1 - Task Provisioning"])
async def get_x1_tasks(ne: str = Query(..., enum=["mme", "sgw", "pgw"])):
    """
    X1 Interface - Network element polls for active tasks
    Returns list of LIID and target information
    """
    warrants = db.get_warrants("Active")
    tasks = []

    for w in warrants:
        tasks.append({
            "liid": w["liid"],
            "warrant_reference": w["warrant_reference"],
            "target_identifier": w["target_identifier_value"],
            "target_type": w["target_identifier_type"],
            "delivery_endpoint": w["delivery_endpoint"]
        })

    logger.info(f"X1 POLL from {ne.upper()}: {len(tasks)} active tasks")
    return {
        "ne": ne,
        "tasks": tasks,
        "poll_interval": 5
    }

# ============================================================================
# X2/X3 Endpoints - Delivery Status
# ============================================================================

@app.get("/x2/iri/log", tags=["X2/X3 - Delivery Status"])
async def get_x2_iri_log():
    """Get X2 IRI event delivery log"""
    events = db.get_iri_log()
    return {
        "status": "SUCCESS",
        "events": events,
        "count": len(events)
    }

@app.delete("/x2/iri/log/clear", tags=["X2/X3 - Delivery Status"])
async def clear_x2_iri_log():
    """Clear X2 IRI event delivery log"""
    count = db.clear_iri_log()
    return {
        "status": "SUCCESS",
        "message": f"Cleared {count} IRI events",
        "cleared_count": count
    }

@app.get("/x3/cc/log", tags=["X2/X3 - Delivery Status"])
async def get_x3_cc_log():
    """Get X3 CC packet delivery log"""
    packets = db.get_cc_log()
    return {
        "status": "SUCCESS",
        "packets": packets,
        "count": len(packets)
    }

@app.delete("/x3/cc/log/clear", tags=["X2/X3 - Delivery Status"])
async def clear_x3_cc_log():
    """Clear X3 CC packet delivery log"""
    count = db.clear_cc_log()
    return {
        "status": "SUCCESS",
        "message": f"Cleared {count} CC packets",
        "cleared_count": count
    }

# ============================================================================
# Health Check
# ============================================================================

@app.get("/health", tags=["Health"])
async def health_check():
    """Server health status"""
    return {
        "status": "OK",
        "service": "LIS-4G Backend API",
        "timestamp": datetime.utcnow().isoformat(),
        "interfaces": {
            "HI1": "active",
            "X1": "active",
            "X2": "active",
            "X3": "active"
        }
    }

# ============================================================================
# X2 TPKT Socket Server
# ============================================================================

def start_x2_server(x2_port: int = 4000):
    """Start X2 TPKT socket server on port 4000"""
    def handle_x2_connection():
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind(('0.0.0.0', x2_port))
        server_socket.listen(5)
        logger.info(f"X2 TPKT Server listening on port {x2_port}")

        while True:
            try:
                client_socket, addr = server_socket.accept()
                logger.info(f"X2 connection from {addr[0]}:{addr[1]}")

                # Receive TPKT packet
                tpkt_header = client_socket.recv(4)
                if len(tpkt_header) >= 4:
                    # Parse TPKT header: version, reserved, length (2 bytes big-endian)
                    length = struct.unpack('!H', tpkt_header[2:4])[0]
                    payload_size = length - 4

                    # Receive payload
                    payload = client_socket.recv(payload_size)

                    try:
                        event_data = json.loads(payload.decode('utf-8'))
                        liid = event_data.get('liid')
                        event_id = event_data.get('event_id')
                        event_name = event_data.get('event_name')

                        if liid and event_id and event_name:
                            db.log_iri_event(liid, event_id, event_name)
                        else:
                            logger.warning(f"Invalid X2 event format: {payload}")
                    except json.JSONDecodeError:
                        logger.error(f"Failed to parse X2 payload: {payload}")

                client_socket.close()
            except Exception as e:
                logger.error(f"X2 server error: {e}")

    # Start server in background thread
    x2_thread = threading.Thread(target=handle_x2_connection, daemon=True)
    x2_thread.start()
    logger.info("X2 TPKT server started")

# ============================================================================
# Main
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description="LIS-4G Backend API Server")
    parser.add_argument("--host", default="0.0.0.0", help="Server host")
    parser.add_argument("--port", type=int, default=8001, help="Server port")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    parser.add_argument("--ftp-host", default="10.80.20.45", help="LEA FTP host")
    parser.add_argument("--ftp-port", type=int, default=21, help="LEA FTP port")

    args = parser.parse_args()

    logger.info("="*70)
    logger.info("LIS-4G Backend API - Starting")
    logger.info(f"Listen: {args.host}:{args.port}")
    logger.info(f"LEA FTP: {args.ftp_host}:{args.ftp_port}")
    logger.info("Interfaces: HI1 (Warrant), X1 (Tasks), X2 (IRI), X3 (CC)")
    logger.info("="*70)

    # Start X2 TPKT socket server
    start_x2_server(x2_port=4000)

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level=args.log_level.lower()
    )

if __name__ == "__main__":
    main()
